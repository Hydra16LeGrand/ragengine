"""Parcours HTTP de `POST /api/v1/retrieval/search` : matrice §4 appliquée bout en bout.

Le moteur d'ingestion est substitué par un double en mémoire (`FakeIngestionEngine`),
monkeypatché sur `app.services.retrieval.resolve_ingestion_engine` — le point
de résolution que `RetrievalService` appelle lui-même quand aucun `engine=...`
n'est injecté (il n'existe pas de dépendance FastAPI dédiée à surcharger via
`app.dependency_overrides` : le routeur construit le service directement,
comme `documents.py` le fait déjà pour `DocumentService`)."""

from __future__ import annotations

import uuid
from http import HTTPStatus

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeBase, User
from app.domain.enums import MemberRole, Visibility
from app.domain.schemas.session import SessionCreate
from app.domain.schemas.user import UserCreate
from app.main import API_PREFIX
from app.providers.engine.fake import FakeIngestionEngine
from app.services import retrieval as retrieval_service
from app.services.identity.sessions import SessionService
from app.services.identity.users import UserService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


SEARCH_URL = f"{API_PREFIX}/retrieval/search"


SESSION_COOKIE = "ragengine_session"


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> FakeIngestionEngine:
    """Substitue le point de résolution du moteur : aucun appel réseau dans ces tests."""
    engine = FakeIngestionEngine(strict=False)
    monkeypatch.setattr(retrieval_service, "resolve_ingestion_engine", lambda settings: engine)
    return engine


async def _register(db: AsyncSession, *, prefix: str = "ret") -> tuple[User, str]:
    """Crée un compte réel persisté et renvoie l'utilisateur avec son adresse."""
    email = factories.unique_email(prefix)
    user = await UserService(db).register(
        UserCreate(email=email, password=SecretStr(PASSWORD), display_name="Amara")
    )
    return user, email


async def _log_in(client: AsyncClient, db: AsyncSession, email: str) -> None:
    """Ouvre une session pour `email` et pose son cookie sur le client."""
    issued = await SessionService(db).login(
        SessionCreate(email=email, password=SecretStr(PASSWORD))
    )
    client.cookies.set(SESSION_COOKIE, issued.token)


async def _kb(db: AsyncSession, owner: User, *, visibility: Visibility) -> KnowledgeBase:
    """Base persistée directement, dataset déjà « ingéré » pour atteindre le moteur."""
    knowledge_base = factories.make_knowledge_base(
        owner, visibility=visibility, slug=f"base-{uuid.uuid4().hex[:8]}"
    )
    knowledge_base.ragflow_dataset_external_id = "dataset-externe"
    db.add(knowledge_base)
    await db.flush()
    return knowledge_base


def search_body(knowledge_base_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    """Corps de requête minimal valide."""
    return {"knowledge_base_id": str(knowledge_base_id), "query": "question"} | overrides


@pytest.mark.parametrize("role", [MemberRole.VIEWER, MemberRole.EDITOR, MemberRole.OWNER])
async def test_a_member_can_search_a_private_base(
    retrieval_client: AsyncClient,
    db_session: AsyncSession,
    fake_engine: FakeIngestionEngine,
    role: MemberRole,
) -> None:
    """viewer/editor/owner : tous peuvent chercher sur une base privée dont ils sont membres."""
    owner, owner_email = await _register(db_session, prefix="owner")
    knowledge_base = await _kb(db_session, owner, visibility=Visibility.PRIVATE)

    if role is MemberRole.OWNER:
        acting_email = owner_email
    else:
        member, member_email = await _register(db_session, prefix="membre")
        db_session.add(factories.make_member(knowledge_base, member, role=role))
        await db_session.flush()
        acting_email = member_email

    await _log_in(retrieval_client, db_session, acting_email)
    response = await retrieval_client.post(SEARCH_URL, json=search_body(knowledge_base.id))

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json() == {"results": []}
    assert len(fake_engine.search_calls) == 1


async def test_an_unrelated_authenticated_user_cannot_see_a_private_base(
    retrieval_client: AsyncClient, db_session: AsyncSession, fake_engine: FakeIngestionEngine
) -> None:
    """Même raisonnement d'invisibilité pour un compte réel sans lien avec la base."""
    owner, _ = await _register(db_session, prefix="private-owner")
    knowledge_base = await _kb(db_session, owner, visibility=Visibility.PRIVATE)
    _, stranger_email = await _register(db_session, prefix="stranger")

    await _log_in(retrieval_client, db_session, stranger_email)
    response = await retrieval_client.post(SEARCH_URL, json=search_body(knowledge_base.id))

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert len(fake_engine.search_calls) == 0
