"""Câblage FastAPI des dépendances d'autorisation.

Ces tests montent des routes jetables : `require_role` et `require_kb_permission`
ne sont consommées par aucun routeur de ce lot, mais elles constituent le contrat
sur lequel s'appuieront les routeurs CRUD. Les vérifier ici garantit que ce
contrat tient dans un arbre de dépendances réel — nom du paramètre de chemin
compris."""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_kb_permission, require_role, require_scope
from app.db.models import User
from app.domain.enums import MemberRole, UserRole
from app.domain.schemas.api_key import ApiKeyCreate
from app.domain.schemas.session import SessionCreate
from app.domain.schemas.user import UserCreate
from app.domain.scopes import ApiKeyScope
from app.main import API_PREFIX
from app.services.identity.api_keys import ApiKeyService
from app.services.identity.permissions import KnowledgeBaseAccess
from app.services.identity.sessions import SessionService
from app.services.identity.users import UserService
from tests.conftest import BASE_URL
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


guarded = APIRouter()


@guarded.get("/admin-only")
async def _admin_only(
    _: Annotated[object, Depends(require_role(UserRole.ADMIN))],
) -> dict[str, str]:
    """Route réservée aux administrateurs de la plateforme."""
    return {"status": "ok"}


@guarded.get("/knowledge-bases/{kb_id}/probe")
async def _probe(
    access: Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.VIEWER))],
) -> dict[str, str | None]:
    """Route de lecture d'une base, protégée par la matrice §4."""
    return {"slug": access.knowledge_base.slug, "role": access.role}


@guarded.patch("/knowledge-bases/{kb_id}/probe")
async def _probe_write(
    access: Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.EDITOR))],
) -> dict[str, str | None]:
    """Route d'écriture sur une base, protégée par la matrice §4."""
    return {"slug": access.knowledge_base.slug, "role": access.role}


@guarded.get("/scope-probe", dependencies=[Depends(require_scope(ApiKeyScope.DOCUMENTS_WRITE))])
async def _scope_probe() -> dict[str, str]:
    """Route jetable protégée par une portée de clé API arbitraire (Phase 9)."""
    return {"status": "ok"}


@pytest.fixture
def guarded_client(identity_app: FastAPI) -> AsyncClient:
    """Client sur l'application d'identité augmentée des routes jetables."""
    identity_app.include_router(guarded, prefix=API_PREFIX)
    return AsyncClient(transport=ASGITransport(app=identity_app), base_url=BASE_URL)


async def _log_in(client: AsyncClient, db: AsyncSession, *, role: UserRole) -> User:
    """Crée un compte du rôle demandé et pose son cookie de session sur le client."""
    email = factories.unique_email("dep")
    user = await UserService(db).register(
        UserCreate(email=email, password=SecretStr(PASSWORD), display_name="Amara")
    )
    user.role = role
    await db.flush()
    issued = await SessionService(db).login(
        SessionCreate(email=email, password=SecretStr(PASSWORD))
    )
    client.cookies.set("ragengine_session", issued.token)
    return user


async def test_require_role_refuses_an_anonymous_visitor(guarded_client: AsyncClient) -> None:
    """Sans authentification, la dépendance répond `401`, pas `403`."""
    async with guarded_client as client:
        response = await client.get(f"{API_PREFIX}/admin-only")

    assert response.status_code == HTTPStatus.UNAUTHORIZED


async def test_require_role_refuses_a_member(
    guarded_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Un `member` authentifié n'accède pas à l'administration : `403`."""
    async with guarded_client as client:
        await _log_in(client, db_session, role=UserRole.MEMBER)
        response = await client.get(f"{API_PREFIX}/admin-only")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["required_role"] == "admin"


async def test_require_role_admits_an_admin(
    guarded_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Cas nominal du rôle plateforme."""
    async with guarded_client as client:
        await _log_in(client, db_session, role=UserRole.ADMIN)
        response = await client.get(f"{API_PREFIX}/admin-only")

    assert response.status_code == HTTPStatus.OK


async def test_require_kb_permission_hides_a_private_base(
    guarded_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Une base privée invisible répond `404` : son existence n'est pas confirmée."""
    owner = await UserService(db_session).register(
        UserCreate(
            email=factories.unique_email("proprio"),
            password=SecretStr(PASSWORD),
            display_name="Propriétaire",
        )
    )
    knowledge_base = factories.make_knowledge_base(owner, slug="privee")
    db_session.add(knowledge_base)
    await db_session.flush()

    async with guarded_client as client:
        await _log_in(client, db_session, role=UserRole.MEMBER)
        response = await client.get(f"{API_PREFIX}/knowledge-bases/{knowledge_base.id}/probe")

    assert response.status_code == HTTPStatus.NOT_FOUND


async def test_require_kb_permission_grants_the_owner(
    guarded_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Le propriétaire écrit sur sa base sans ligne d'appartenance explicite."""
    async with guarded_client as client:
        owner = await _log_in(client, db_session, role=UserRole.MEMBER)
        knowledge_base = factories.make_knowledge_base(owner, slug="a-moi")
        db_session.add(knowledge_base)
        await db_session.flush()

        response = await client.patch(f"{API_PREFIX}/knowledge-bases/{knowledge_base.id}/probe")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["role"] == "owner"


async def test_an_unknown_base_answers_not_found(
    guarded_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Un identifiant inventé traverse la dépendance sans provoquer de 500."""
    async with guarded_client as client:
        await _log_in(client, db_session, role=UserRole.MEMBER)
        response = await client.get(f"{API_PREFIX}/knowledge-bases/{uuid.uuid4()}/probe")

    assert response.status_code == HTTPStatus.NOT_FOUND


async def _issue_api_key(db: AsyncSession, *, scopes: list[str]) -> str:
    """Crée un compte réel et une clé API portant `scopes`, renvoie le jeton en clair."""
    user = await UserService(db).register(
        UserCreate(
            email=factories.unique_email("scope"),
            password=SecretStr(PASSWORD),
            display_name="Porteur de clé",
        )
    )
    issued = await ApiKeyService(db).create(user, ApiKeyCreate(name="Clé de test", scopes=scopes))
    return issued.token


async def test_require_scope_refuses_a_key_without_the_scope(
    guarded_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Une clé qui n'a pas la portée exigée répond `403`, portée manquante en extension."""
    token = await _issue_api_key(db_session, scopes=[ApiKeyScope.KNOWLEDGE_BASES_READ.value])

    async with guarded_client as client:
        response = await client.get(
            f"{API_PREFIX}/scope-probe", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["required_scope"] == ApiKeyScope.DOCUMENTS_WRITE.value


async def test_require_scope_admits_a_key_with_the_exact_scope(
    guarded_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Cas nominal : la clé porte exactement la portée exigée."""
    token = await _issue_api_key(db_session, scopes=[ApiKeyScope.DOCUMENTS_WRITE.value])

    async with guarded_client as client:
        response = await client.get(
            f"{API_PREFIX}/scope-probe", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == HTTPStatus.OK


async def test_require_scope_admits_the_wildcard_scope(
    guarded_client: AsyncClient, db_session: AsyncSession
) -> None:
    """`\"*\"` reste un joker total — rétrocompatibilité des clés pré-Phase 9."""
    token = await _issue_api_key(db_session, scopes=["*"])

    async with guarded_client as client:
        response = await client.get(
            f"{API_PREFIX}/scope-probe", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == HTTPStatus.OK


async def test_require_scope_is_a_noop_outside_api_key_authentication(
    guarded_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Un principal de session (cookie) n'a pas de clé API : la portée ne s'applique pas."""
    async with guarded_client as client:
        await _log_in(client, db_session, role=UserRole.MEMBER)
        response = await client.get(f"{API_PREFIX}/scope-probe")

    assert response.status_code == HTTPStatus.OK
