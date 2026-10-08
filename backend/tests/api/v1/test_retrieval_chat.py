"""Parcours HTTP de `POST /api/v1/retrieval/chat` : flux SSE, ordre des évènements, erreurs.

Le moteur d'ingestion et le fournisseur LLM sont tous deux substitués par des doubles en
mémoire, monkeypatchés sur leurs points de résolution respectifs dans
`app.services.retrieval` — même technique que `fake_engine` dans `test_retrieval.py`
(`resolve_ingestion_engine`), étendue ici à `resolve_llm_provider`. `FakeIngestionEngine`,
`ScriptedFakeLLM` et les auxiliaires d'inscription/connexion sont **dupliqués** depuis
`test_retrieval.py` plutôt qu'importés : convention affichée par ce fichier lui-même
(auxiliaires préfixés `_`, jamais partagés entre modules de test).

Aucune dépendance `sse-starlette`/client SSE n'existe dans ce dépôt (brief) : le corps de
la réponse est parsé à la main (`_parse_sse`), au niveau texte, tel qu'un client le
recevrait réellement — `httpx.AsyncClient.post` en transport ASGI consomme le générateur
jusqu'au bout et bufférise, donc `response.text` porte le flux complet."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeBase, User
from app.domain.enums import Visibility
from app.domain.schemas.session import SessionCreate
from app.domain.schemas.user import UserCreate
from app.main import API_PREFIX
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.llm.base import ChatMessage, LLMCapabilities, LLMError
from app.services import retrieval as retrieval_service
from app.services.identity.sessions import SessionService
from app.services.identity.users import UserService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


CHAT_URL = f"{API_PREFIX}/retrieval/chat"


SESSION_COOKIE = "ragengine_session"


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> FakeIngestionEngine:
    """Substitue le point de résolution du moteur : aucun appel réseau dans ces tests."""
    engine = FakeIngestionEngine(strict=False)
    monkeypatch.setattr(retrieval_service, "resolve_ingestion_engine", lambda settings: engine)
    return engine


@dataclass(slots=True)
class ScriptedFakeLLM:
    """Double minimal de `LLMPort` : rejoue des fragments scénarisés, peut échouer en cours
    de flux (`fail_with`, levée après le dernier fragment de `fragments`)."""

    fragments: Sequence[str] = ()
    fail_with: Exception | None = None
    capabilities: LLMCapabilities = field(
        default_factory=lambda: LLMCapabilities(
            provider_id="fake-chat-llm",
            vendor="anthropic",
            context_window=200_000,
            supports_streaming=True,
        )
    )

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        raise NotImplementedError("Non exercé par ces tests.")

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        for fragment in self.fragments:
            yield fragment
        if self.fail_with is not None:
            raise self.fail_with


def _patch_llm(monkeypatch: pytest.MonkeyPatch, llm: ScriptedFakeLLM) -> None:
    """Substitue le point de résolution du LLM — même technique que `fake_engine`."""
    monkeypatch.setattr(
        retrieval_service,
        "resolve_llm_provider",
        lambda provider_id, settings, **_kwargs: llm,
    )


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedFakeLLM:
    """Fournisseur LLM scénarisé par défaut : deux fragments, aucune panne."""
    llm = ScriptedFakeLLM(fragments=("Bonjour", " le monde"))
    _patch_llm(monkeypatch, llm)
    return llm


async def _register(db: AsyncSession, *, prefix: str = "chat") -> tuple[User, str]:
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
        owner, visibility=visibility, slug=f"chat-{uuid.uuid4().hex[:8]}"
    )
    knowledge_base.ragflow_dataset_external_id = "dataset-externe"
    db.add(knowledge_base)
    await db.flush()
    return knowledge_base


def chat_body(knowledge_base_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    """Corps de requête minimal valide pour `/retrieval/chat`.

    `llm_provider_id` par défaut est un identifiant **réel** du catalogue :
    depuis l'addendum 2026-09-21 à ADR-054, `resolve_llm_funding` rejette tout
    identifiant inconnu avant même de décider qui paie, y compris quand
    `resolve_llm_provider` est monkeypatché (`_patch_llm` ci-dessous) — cette
    validation-là n'est jamais contournée par le double."""
    return {
        "knowledge_base_id": str(knowledge_base_id),
        "messages": [{"role": "user", "content": "question"}],
        "llm_provider_id": "claude-opus-5",
    } | overrides


def _parse_sse(body: str) -> list[tuple[str, object]]:
    """Découpe un flux SSE brut en `(event, data-désérialisé-JSON)`, dans l'ordre reçu."""
    events: list[tuple[str, object]] = []
    for block in body.strip("\n").split("\n\n"):
        if not block:
            continue
        event_line, data_line = block.split("\n", 1)
        assert event_line.startswith("event: ")
        assert data_line.startswith("data: ")
        events.append(
            (event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: ")))
        )
    return events


async def test_a_member_chat_receives_sources_then_tokens_then_done_in_order(
    retrieval_client: AsyncClient,
    db_session: AsyncSession,
    fake_engine: FakeIngestionEngine,
    fake_llm: ScriptedFakeLLM,
) -> None:
    """Cas nominal : SSE bien formé, ordre `sources` → `token`* → `done`, statut `200`."""
    owner, owner_email = await _register(db_session, prefix="owner")
    knowledge_base = await _kb(db_session, owner, visibility=Visibility.PRIVATE)
    await _log_in(retrieval_client, db_session, owner_email)

    response = await retrieval_client.post(CHAT_URL, json=chat_body(knowledge_base.id))

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)

    assert events[0] == ("sources", [])
    assert events[1] == ("token", "Bonjour")
    assert events[2] == ("token", " le monde")
    assert events[3] == ("done", {})
    assert len(fake_engine.search_calls) == 1


async def test_an_unknown_llm_provider_id_is_rejected_before_any_streaming(
    retrieval_client: AsyncClient, db_session: AsyncSession, fake_engine: FakeIngestionEngine
) -> None:
    """`llm_provider_id` inconnu : `422` classique, le moteur de recherche n'est pas dérangé
    (résolution du LLM avant la recherche) — pas de `fake_llm` ici : la résolution retombe
    sur le vrai `resolve_llm_provider`, sans réseau."""
    owner, owner_email = await _register(db_session, prefix="owner")
    knowledge_base = await _kb(db_session, owner, visibility=Visibility.PRIVATE)
    await _log_in(retrieval_client, db_session, owner_email)

    response = await retrieval_client.post(
        CHAT_URL, json=chat_body(knowledge_base.id, llm_provider_id="does-not-exist")
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "inconnu" in response.json()["detail"]
    assert len(fake_engine.search_calls) == 0


async def test_messages_not_ending_on_a_user_turn_is_a_422_validation_error(
    retrieval_client: AsyncClient, db_session: AsyncSession, fake_engine: FakeIngestionEngine
) -> None:
    """Validation Pydantic du corps : une conversation finissant par `assistant` est refusée."""
    owner, owner_email = await _register(db_session, prefix="owner")
    knowledge_base = await _kb(db_session, owner, visibility=Visibility.PRIVATE)
    await _log_in(retrieval_client, db_session, owner_email)

    response = await retrieval_client.post(
        CHAT_URL,
        json=chat_body(
            knowledge_base.id,
            messages=[
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "réponse"},
            ],
        ),
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.headers["content-type"].startswith("application/problem+json")
    assert len(fake_engine.search_calls) == 0


async def test_an_llm_failure_mid_stream_becomes_an_sse_error_event_without_a_done(
    retrieval_client: AsyncClient,
    db_session: AsyncSession,
    fake_engine: FakeIngestionEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La réponse est déjà `200` : une panne du fournisseur en cours de flux ne peut plus
    changer le code HTTP, elle devient un évènement `error` — et le flux s'arrête là, sans
    `done` : c'est ce qui distingue côté client une fin propre d'une coupure."""
    owner, owner_email = await _register(db_session, prefix="owner")
    knowledge_base = await _kb(db_session, owner, visibility=Visibility.PRIVATE)
    await _log_in(retrieval_client, db_session, owner_email)
    llm = ScriptedFakeLLM(fragments=("Début",), fail_with=LLMError("Panne simulée du fournisseur."))
    _patch_llm(monkeypatch, llm)

    response = await retrieval_client.post(CHAT_URL, json=chat_body(knowledge_base.id))

    assert response.status_code == HTTPStatus.OK, response.text
    events = _parse_sse(response.text)

    assert events[0] == ("sources", [])
    assert events[1] == ("token", "Début")
    error_event, error_data = events[2]
    assert error_event == "error"
    assert error_data == {
        "type": "/problems/llm-error",
        "title": "Bad Gateway",
        "status": HTTPStatus.BAD_GATEWAY,
        "detail": "Panne simulée du fournisseur.",
    }
    assert [event for event, _ in events] == ["sources", "token", "error"]
    assert len(fake_engine.search_calls) == 1
