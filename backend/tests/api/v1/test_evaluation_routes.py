"""Parcours HTTP de `POST/GET .../evaluations` : bascule synchrone/asynchrone (ADR-028).

Phase 12 Étape 4 (item restant) : `POST .../evaluations` reste synchrone pour
`sample_size ≤ MAX_EVALUATION_SAMPLE_SIZE` (comportement inchangé depuis la
Phase 8, déjà couvert en détail par `tests/services/test_evaluations.py`) et
bascule en asynchrone au-delà (`202`, run `pending`). Ces tests portent
spécifiquement sur ce que le **routeur** décide — code de statut, forme du
corps, publication Celery — pas sur la génération elle-même (moteur/LLM
factices, jamais exercés au-delà de la validation d'entrée dans les scénarios
asynchrones : aucun item n'est produit avant l'exécution Celery, elle-même
interceptée par `dispatched_tasks`, `tests/conftest.py`).

Moteur et fournisseur LLM sont substitués sur leurs points de résolution dans
`app.services.evaluations` — même technique que `test_retrieval_chat.py`
(`resolve_ingestion_engine`, `resolve_llm_provider`)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Document, KnowledgeBase, User
from app.domain.enums import DocumentStatus
from app.domain.schemas.evaluation import MAX_EVALUATION_SAMPLE_SIZE
from app.domain.schemas.session import SessionCreate
from app.domain.schemas.user import UserCreate
from app.main import API_PREFIX
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.llm.base import ChatMessage, LLMCapabilities
from app.services import evaluations as evaluations_service
from app.services.identity.sessions import SessionService
from app.services.identity.users import UserService
from app.workers.celery_app import RUN_ASYNC_EVALUATION_TASK
from tests.conftest import DispatchedTasks
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


SESSION_COOKIE = "ragengine_session"


KB_URL = f"{API_PREFIX}/knowledge-bases"


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> FakeIngestionEngine:
    """Substitue le point de résolution du moteur : `RAGFLOW_API_KEY` est vide en test."""
    engine = FakeIngestionEngine(
        strict=False,
    )
    monkeypatch.setattr(evaluations_service, "resolve_ingestion_engine", lambda settings: engine)
    return engine


@dataclass(slots=True)
class FakeLLM:
    """Double minimal : seule `capabilities` est lue (résolution/validation)."""

    capabilities: LLMCapabilities = field(
        default_factory=lambda: LLMCapabilities(
            provider_id="fake-eval-llm",
            vendor="anthropic",
            context_window=100_000,
            supports_streaming=True,
        )
    )

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        raise NotImplementedError("Non exercé : aucun item n'est généré côté HTTP dans ces tests.")

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        raise NotImplementedError("Non exercé : aucun item n'est généré côté HTTP dans ces tests.")


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    llm = FakeLLM()
    monkeypatch.setattr(
        evaluations_service, "resolve_llm_provider", lambda provider_id, settings, **_kw: llm
    )
    return llm


async def _register(db: AsyncSession, *, prefix: str = "eval") -> tuple[User, str]:
    email = factories.unique_email(prefix)
    user = await UserService(db).register(
        UserCreate(email=email, password=SecretStr(PASSWORD), display_name="Amara")
    )
    return user, email


async def _log_in(client: AsyncClient, db: AsyncSession, email: str) -> None:
    issued = await SessionService(db).login(
        SessionCreate(email=email, password=SecretStr(PASSWORD))
    )
    client.cookies.set(SESSION_COOKIE, issued.token)


async def _enabled_kb(db: AsyncSession, owner: User) -> KnowledgeBase:
    knowledge_base = factories.make_knowledge_base(owner, slug=f"eval-{uuid.uuid4().hex[:8]}")
    knowledge_base.evaluation_enabled = True
    knowledge_base.ragflow_dataset_external_id = "dataset-externe"
    db.add(knowledge_base)
    await db.flush()
    return knowledge_base


async def _eligible_document(
    db: AsyncSession, knowledge_base: KnowledgeBase, *, external_id: str
) -> Document:
    document = factories.make_document(knowledge_base, filename=f"{external_id}.pdf")
    document.status = DocumentStatus.INDEXED
    document.ragflow_document_external_id = external_id
    db.add(document)
    await db.flush()
    return document


def _evaluations_url(knowledge_base_id: uuid.UUID) -> str:
    return f"{KB_URL}/{knowledge_base_id}/evaluations"


async def test_a_sample_size_above_the_synchronous_cap_is_accepted_asynchronously(
    evaluation_client: AsyncClient,
    db_session: AsyncSession,
    fake_engine: FakeIngestionEngine,
    fake_llm: FakeLLM,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """`sample_size > 20` : `202`, run `pending` sans item, tâche Celery publiée."""
    owner, email = await _register(db_session)
    knowledge_base = await _enabled_kb(db_session, owner)
    await _eligible_document(db_session, knowledge_base, external_id="doc-a")
    await _log_in(evaluation_client, db_session, email)

    response = await evaluation_client.post(
        _evaluations_url(knowledge_base.id),
        json={
            "sample_size": MAX_EVALUATION_SAMPLE_SIZE + 1,
            "llm_provider_id": "claude-opus-5",
        },
    )

    assert response.status_code == HTTPStatus.ACCEPTED, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["items"] == []
    assert body["sample_size"] == 1  # ramené au seul document éligible
    assert body["item_count"] == 0
    assert body["average_judge_score"] is None
    assert body["retrieval_recall_rate"] is None
    assert dispatched_tasks.names == [RUN_ASYNC_EVALUATION_TASK]

    # `GET .../evaluations/{id}` doit refléter ce `pending` proprement — pas d'erreur.
    read_back = await evaluation_client.get(f"{_evaluations_url(knowledge_base.id)}/{body['id']}")
    assert read_back.status_code == HTTPStatus.OK
    assert read_back.json()["status"] == "pending"

    history = await evaluation_client.get(_evaluations_url(knowledge_base.id))
    assert history.status_code == HTTPStatus.OK
    assert history.json()[0]["status"] == "pending"


async def test_a_sample_size_within_the_synchronous_cap_never_dispatches_a_task(
    evaluation_client: AsyncClient,
    db_session: AsyncSession,
    fake_engine: FakeIngestionEngine,
    fake_llm: FakeLLM,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """`sample_size ≤ 20` reste synchrone : `409` (aucun document) sans jamais publier Celery.

    N'exerce pas le chemin nominal jusqu'au bout (déjà couvert par
    `tests/services/test_evaluations.py`) : `FakeLLM`/`FakeIngestionEngine` n'ont pas
    besoin d'être fonctionnels au-delà de la construction du service pour
    vérifier que le routeur choisit bien `create_run`, jamais
    `create_pending_run`, sous le plafond.
    """
    owner, email = await _register(db_session)
    knowledge_base = await _enabled_kb(db_session, owner)
    await _log_in(evaluation_client, db_session, email)

    response = await evaluation_client.post(
        _evaluations_url(knowledge_base.id),
        json={"sample_size": MAX_EVALUATION_SAMPLE_SIZE, "llm_provider_id": "claude-opus-5"},
    )

    assert response.status_code == HTTPStatus.CONFLICT, response.text
    assert dispatched_tasks.names == []


async def test_a_sample_size_beyond_the_configured_async_cap_is_a_validation_error(
    evaluation_client: AsyncClient,
    db_session: AsyncSession,
    fake_engine: FakeIngestionEngine,
    fake_llm: FakeLLM,
    dispatched_tasks: DispatchedTasks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le plafond asynchrone déployé reste appliqué : `422` au-delà, aucune tâche publiée."""
    monkeypatch.setenv("EVALUATION_ASYNC_MAX_SAMPLE_SIZE", "25")
    get_settings.cache_clear()
    owner, email = await _register(db_session)
    knowledge_base = await _enabled_kb(db_session, owner)
    await _eligible_document(db_session, knowledge_base, external_id="doc-a")
    await _log_in(evaluation_client, db_session, email)

    try:
        response = await evaluation_client.post(
            _evaluations_url(knowledge_base.id),
            json={"sample_size": 26, "llm_provider_id": "claude-opus-5"},
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
        assert dispatched_tasks.names == []
    finally:
        get_settings.cache_clear()


async def test_evaluation_disabled_refuses_even_an_asynchronous_sample_size(
    evaluation_client: AsyncClient,
    db_session: AsyncSession,
    fake_engine: FakeIngestionEngine,
    fake_llm: FakeLLM,
    dispatched_tasks: DispatchedTasks,
) -> None:
    owner, email = await _register(db_session)
    knowledge_base = factories.make_knowledge_base(owner, slug=f"eval-off-{uuid.uuid4().hex[:8]}")
    db_session.add(knowledge_base)
    await db_session.flush()
    assert knowledge_base.evaluation_enabled is False
    await _log_in(evaluation_client, db_session, email)

    response = await evaluation_client.post(
        _evaluations_url(knowledge_base.id),
        json={
            "sample_size": MAX_EVALUATION_SAMPLE_SIZE + 1,
            "llm_provider_id": "claude-opus-5",
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
    assert dispatched_tasks.names == []


def _summary_url() -> str:
    return f"{API_PREFIX}/evaluations/summary"


async def test_the_summary_only_aggregates_the_principals_visible_bases(
    evaluation_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Un membre ne voit, dans l'agrégat, que ce à quoi il a accès (CONTRIBUTING.md)."""
    owner, owner_email = await _register(db_session, prefix="summary-owner")
    _stranger, stranger_email = await _register(db_session, prefix="summary-stranger")

    knowledge_base = factories.make_knowledge_base(owner, slug=f"summary-{uuid.uuid4().hex[:8]}")
    db_session.add(knowledge_base)
    await db_session.flush()
    document = factories.make_document(knowledge_base, filename="a.pdf")
    db_session.add(document)
    await db_session.flush()
    run = factories.make_evaluation_run(knowledge_base, requested_by=owner)
    db_session.add(run)
    await db_session.flush()
    db_session.add(factories.make_evaluation_item(run, document, retrieval_rank=1))
    await db_session.flush()

    await _log_in(evaluation_client, db_session, owner_email)
    owner_response = await evaluation_client.get(_summary_url())
    assert owner_response.status_code == HTTPStatus.OK, owner_response.text
    owner_body = owner_response.json()
    assert owner_body["overall"]["item_count"] == 1
    assert [entry["knowledge_base_id"] for entry in owner_body["knowledge_bases"]] == [
        str(knowledge_base.id)
    ]

    evaluation_client.cookies.clear()
    await _log_in(evaluation_client, db_session, stranger_email)
    stranger_response = await evaluation_client.get(_summary_url())
    assert stranger_response.status_code == HTTPStatus.OK, stranger_response.text
    stranger_body = stranger_response.json()
    assert stranger_body["knowledge_bases"] == []
    assert stranger_body["overall"]["item_count"] == 0
