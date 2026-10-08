"""Routine d'évaluation programmée (Phase 12 Étape 4) : les deux tâches Celery réelles.

Mêmes fixtures que `test_ingestion_tasks.py` (`worker_sessions`, `eager_celery`) :
les tâches sont exécutées à travers leur propre boucle d'événements
(`asyncio.run`, `run_in_session`), contre une base réellement commitée.

`EvaluationService`, construite avec ses résolveurs par défaut, ferait de vrais
appels réseau (RAGFlow, fournisseur LLM) : les tests substituent
`app.workers.tasks.evaluations._service` par un service déjà construit avec un
moteur (`FakeIngestionEngine`) et un LLM factices — même principe que
`fake_engine`/`fake_storage` pour le pipeline d'ingestion (`tests/workers/conftest.py`).

Ce que ces tests verrouillent (brief Phase 12 Étape 4) :

- la tâche périodique ne déclenche que les bases `evaluation_enabled=true` ;
- un run en échec (budget épuisé / disjoncteur ouvert, simulé par
  `LLMRateLimitError`) ne bloque pas les bases suivantes du même dispatch, et
  se termine sans relever d'exception à Celery (pas de retry agressif) ;
- le volume par run reste celui de `Settings.evaluation_scheduled_sample_size`,
  jamais plus, même quand davantage de documents sont éligibles.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import EvaluationRun, User
from app.domain.enums import DocumentStatus, EvaluationRunStatus
from app.providers.engine.base import (
    EngineChunk,
)
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.llm.base import ChatMessage, LLMCapabilities, LLMRateLimitError
from app.services.evaluations import EvaluationService
from app.services.identity.platform_llm_budget import (
    peek_evaluation_quota,
    peek_global_budget,
    peek_user_quota,
)
from app.workers.tasks import evaluations
from tests.db.factories import make_document, make_guest, make_knowledge_base, make_user
from tests.workers.conftest import SessionFactory, run_sync

pytestmark = pytest.mark.usefixtures("explicit_llm_caps")

PASSAGE_OK = "Le télétravail est autorisé jusqu'à trois jours par semaine."
PASSAGE_FAIL = "La politique de remboursement accorde 14 jours sans justification."


def _capabilities(provider_id: str) -> LLMCapabilities:
    return LLMCapabilities(
        provider_id=provider_id, vendor="groq", context_window=32_000, supports_streaming=True
    )


@dataclass(slots=True)
class SwitchingLLM:
    """`LLMPort` scripté : lève `LLMRateLimitError` pour les passages de `failing_passages`.

    Simule un fournisseur à sec (budget épuisé / disjoncteur ouvert) sans
    dépendre du vrai disjoncteur (`app.providers.llm.circuit_breaker`) : ce
    test verrouille le comportement de la tâche Celery vis-à-vis de
    n'importe quelle exception `create_run`, pas la mécanique du disjoncteur
    elle-même (déjà couverte ailleurs).
    """

    generation_by_passage: dict[str, str]
    judge_response: str
    failing_passages: frozenset[str] = frozenset()
    stream_text: str = "réponse générée par le chat"
    capabilities: LLMCapabilities = field(default_factory=lambda: _capabilities("scripted-llm"))

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        user_content = messages[-1].content
        if user_content in self.failing_passages:
            message = f"Quota Groq épuisé pour le modèle « {self.capabilities.provider_id} »."
            raise LLMRateLimitError(message)
        if user_content.startswith("Question : "):
            return self.judge_response
        return self.generation_by_passage[user_content]

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        yield self.stream_text


def generation_json(question: str, answer: str) -> str:
    return json.dumps({"question": question, "answer": answer})


def judge_json(score: float, rationale: str) -> str:
    return json.dumps({"score": score, "rationale": rationale})


@dataclass(frozen=True, slots=True)
class SeededEvaluationKb:
    knowledge_base_id: uuid.UUID
    owner_id: uuid.UUID


async def _seed_evaluation_kb(
    db: AsyncSession,
    *,
    slug: str,
    document_external_ids: Sequence[str],
    evaluation_enabled: bool = True,
    owner_is_guest: bool = False,
    owner_is_active: bool = True,
) -> SeededEvaluationKb:
    """Propriétaire + base + N documents `INDEXED` avec référence moteur (`create_run` l'exige)."""

    owner = make_guest() if owner_is_guest else make_user()
    owner.is_active = owner_is_active
    db.add(owner)
    await db.flush([owner])

    knowledge_base = make_knowledge_base(owner, slug=slug)
    knowledge_base.evaluation_enabled = evaluation_enabled
    knowledge_base.ragflow_dataset_external_id = "dataset-externe"
    db.add(knowledge_base)
    await db.flush()

    for external_id in document_external_ids:
        document = make_document(knowledge_base, filename=f"{external_id}.pdf")
        document.status = DocumentStatus.INDEXED
        document.ragflow_document_external_id = external_id
        db.add(document)
    await db.flush()

    return SeededEvaluationKb(knowledge_base_id=knowledge_base.id, owner_id=owner.id)


async def _purge(db: AsyncSession, owner_id: uuid.UUID) -> None:
    """Efface le propriétaire : la cascade emporte base, documents et runs d'évaluation."""
    await db.execute(select(User).where(User.id == owner_id))  # sanity: existe avant suppression
    from sqlalchemy import delete

    await db.execute(delete(User).where(User.id == owner_id))


async def _runs_for(db: AsyncSession, knowledge_base_id: uuid.UUID) -> list[EvaluationRun]:
    statement = select(EvaluationRun).where(EvaluationRun.knowledge_base_id == knowledge_base_id)
    return list((await db.execute(statement)).scalars().all())


async def _seed_pending_run(
    db: AsyncSession,
    knowledge_base_id: uuid.UUID,
    requested_by_id: uuid.UUID | None,
    *,
    sample_size: int = 1,
) -> uuid.UUID:
    """Persiste un `EvaluationRun` `pending`, comme le ferait `create_pending_run`.

    Construit directement le modèle plutôt que d'appeler
    `EvaluationService.create_pending_run` : ces tests portent sur la tâche
    Celery qui **consomme** un run déjà `pending`, pas sur sa création (déjà
    couverte par `tests/services/test_evaluations.py`).
    """
    run = EvaluationRun(
        knowledge_base_id=knowledge_base_id,
        requested_by_id=requested_by_id,
        llm_provider_id="claude-opus-5",
        sample_size=sample_size,
        status=EvaluationRunStatus.PENDING,
    )
    db.add(run)
    await db.flush([run])
    return run.id


# ─── `run_scheduled_evaluation` : cas nominal ────────────────────────────────


def test_a_nominal_scheduled_run_creates_an_evaluation_run(
    worker_sessions: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un run programmé rejoue `create_run` et persiste bien un `EvaluationRun`.

    `evaluation_scheduled_llm_provider_id` (Groq par défaut, choisi pour ne jamais dépendre
    du crédit Anthropic partagé — cf. sa docstring dans `app/core/config.py`) n'est qu'une
    **demande** : depuis l'addendum 2026-09-21 à ADR-054, le propriétaire de la base n'a
    généralement pas de clé personnelle pour ce fournisseur, la routine tourne donc hors clé
    personnelle et le modèle effectif est imposé à `platform_llm_model_id` — même règle que
    pour un appel de chat. C'est une tension assumée, pas un bug de ce test : la routine
    partage désormais le même modèle forcé que le reste du trafic hors clé personnelle,
    au prix de la diversification de fournisseur que `evaluation_scheduled_llm_provider_id`
    visait à l'origine."""
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(db, slug="kb-nominal", document_external_ids=["doc-a"]),
    )
    engine = FakeIngestionEngine(
        strict=False, chunks_by_document={"doc-a": [EngineChunk(chunk_id="c1", content=PASSAGE_OK)]}
    )
    llm = SwitchingLLM(
        generation_by_passage={
            PASSAGE_OK: generation_json("Combien de jours de télétravail ?", "Trois jours.")
        },
        judge_response=judge_json(0.9, "Réponse fidèle."),
    )
    monkeypatch.setattr(
        evaluations,
        "_service",
        lambda db, settings: EvaluationService(db, settings, engine=engine, llm=llm),
    )

    try:
        outcome = evaluations.run_scheduled_evaluation(str(kb.knowledge_base_id))

        assert outcome == "completed"
        runs = run_sync(worker_sessions, lambda db: _runs_for(db, kb.knowledge_base_id))
        assert len(runs) == 1
        settings = get_settings()
        assert runs[0].llm_provider_id == settings.platform_llm_model_id
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))


def test_a_disabled_or_deleted_knowledge_base_is_a_silent_no_op(
    worker_sessions: SessionFactory,
) -> None:
    """Course dispatch → exécution : base désactivée entre-temps, pas une erreur."""
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db, slug="kb-disabled", document_external_ids=["doc-a"], evaluation_enabled=False
        ),
    )

    try:
        outcome = evaluations.run_scheduled_evaluation(str(kb.knowledge_base_id))

        assert outcome == "completed"
        assert run_sync(worker_sessions, lambda db: _runs_for(db, kb.knowledge_base_id)) == []
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))

    # Base entièrement absente (jamais désactivée, jamais existé) : même issue.
    assert evaluations.run_scheduled_evaluation(str(uuid.uuid4())) == "completed"


# ─── Robustesse budget/disjoncteur : une base en échec n'en bloque pas d'autres ──


def test_a_failing_provider_does_not_block_the_next_knowledge_base(
    worker_sessions: SessionFactory,
    eager_celery: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Le fan-out du dispatch traite chaque base indépendamment (brief Phase 12 Étape 4)."""
    failing_kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(db, slug="kb-failing", document_external_ids=["doc-fail"]),
    )
    healthy_kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(db, slug="kb-healthy", document_external_ids=["doc-ok"]),
    )
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={
            "doc-fail": [EngineChunk(chunk_id="c1", content=PASSAGE_FAIL)],
            "doc-ok": [EngineChunk(chunk_id="c2", content=PASSAGE_OK)],
        },
    )
    llm = SwitchingLLM(
        generation_by_passage={
            PASSAGE_OK: generation_json("Combien de jours de télétravail ?", "Trois jours.")
        },
        judge_response=judge_json(0.9, "Réponse fidèle."),
        failing_passages=frozenset({PASSAGE_FAIL}),
    )
    monkeypatch.setattr(
        evaluations,
        "_service",
        lambda db, settings: EvaluationService(db, settings, engine=engine, llm=llm),
    )
    caplog.set_level(logging.ERROR, logger="app.workers.tasks.evaluations")

    try:
        dispatched = evaluations.dispatch_scheduled_evaluations()

        assert dispatched == 2
        # La base saine a bien produit un run — le dispatch de la suivante n'a
        # jamais été empêché par l'échec de la première (ordre `created_at`).
        healthy_runs = run_sync(
            worker_sessions, lambda db: _runs_for(db, healthy_kb.knowledge_base_id)
        )
        assert len(healthy_runs) == 1
        # La base en échec n'a produit aucun run : `create_run` a levé avant
        # toute persistance (le seul document éligible est celui qui échoue).
        failing_runs = run_sync(
            worker_sessions, lambda db: _runs_for(db, failing_kb.knowledge_base_id)
        )
        assert failing_runs == []
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, failing_kb.owner_id))
        run_sync(worker_sessions, lambda db: _purge(db, healthy_kb.owner_id))

    # L'échec est journalisé avec le repère stable, pas seulement avalé en silence.
    failure_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == evaluations.SCHEDULED_EVALUATION_FAILURE_MARKER
    ]
    assert len(failure_records) == 1
    assert failure_records[0].knowledge_base_id == str(failing_kb.knowledge_base_id)
    assert failure_records[0].error_type == "LLMRateLimitError"


def test_only_evaluation_enabled_bases_are_dispatched(
    worker_sessions: SessionFactory, eager_celery: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le flag `evaluation_enabled` gouverne seul le dispatch — pas de second mécanisme."""
    enabled_kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(db, slug="kb-enabled", document_external_ids=["doc-a"]),
    )
    disabled_kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db, slug="kb-disabled-2", document_external_ids=["doc-b"], evaluation_enabled=False
        ),
    )
    dispatched_ids: list[str] = []
    monkeypatch.setattr(
        evaluations,
        "run_scheduled_evaluation",
        type("_Task", (), {"delay": staticmethod(dispatched_ids.append)}),
    )

    try:
        dispatched = evaluations.dispatch_scheduled_evaluations()

        assert dispatched == 1
        assert dispatched_ids == [str(enabled_kb.knowledge_base_id)]
        assert str(disabled_kb.knowledge_base_id) not in dispatched_ids
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, enabled_kb.owner_id))
        run_sync(worker_sessions, lambda db: _purge(db, disabled_kb.owner_id))


# ─── Volume par run : jamais plus que `Settings.evaluation_scheduled_sample_size` ──


def test_the_scheduled_sample_size_caps_the_run_even_with_more_eligible_documents(
    worker_sessions: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trois documents éligibles, `sample_size=2` : le run en tire deux, pas trois."""
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db, slug="kb-capped", document_external_ids=["doc-1", "doc-2", "doc-3"]
        ),
    )
    passages = {
        "doc-1": "Passage un.",
        "doc-2": "Passage deux.",
        "doc-3": "Passage trois.",
    }
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={
            external_id: [EngineChunk(chunk_id=external_id, content=text)]
            for external_id, text in passages.items()
        },
    )
    llm = SwitchingLLM(
        generation_by_passage={
            text: generation_json("Question ?", "Réponse.") for text in passages.values()
        },
        judge_response=judge_json(0.7, "Correct."),
    )
    monkeypatch.setattr(
        evaluations,
        "_service",
        lambda db, settings: EvaluationService(db, settings, engine=engine, llm=llm),
    )
    capped_settings = Settings(evaluation_scheduled_sample_size=2)
    monkeypatch.setattr(evaluations, "get_settings", lambda: capped_settings)

    try:
        outcome = evaluations.run_scheduled_evaluation(str(kb.knowledge_base_id))

        assert outcome == "completed"
        runs = run_sync(worker_sessions, lambda db: _runs_for(db, kb.knowledge_base_id))
        assert len(runs) == 1
        assert runs[0].sample_size == 2
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))


def test_the_default_scheduled_sample_size_stays_within_the_manual_cap() -> None:
    """8 par défaut : dans l'ordre de grandeur documenté (5-10), sous le plafond synchrone (20)."""
    settings = Settings()

    assert 1 <= settings.evaluation_scheduled_sample_size <= 20
    assert settings.evaluation_scheduled_sample_size == 8


# ─── `run_async_evaluation` : run à la demande, au-delà du plafond synchrone ─────
# Phase 12 Étape 4 (item restant), ADR-028.


def test_a_pending_async_run_is_completed(
    worker_sessions: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rejoue un run déjà `pending` — publié par `EvaluationService.create_pending_run`."""
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(db, slug="kb-async-ok", document_external_ids=["doc-a"]),
    )
    run_id = run_sync(
        worker_sessions, lambda db: _seed_pending_run(db, kb.knowledge_base_id, kb.owner_id)
    )
    engine = FakeIngestionEngine(
        strict=False, chunks_by_document={"doc-a": [EngineChunk(chunk_id="c1", content=PASSAGE_OK)]}
    )
    llm = SwitchingLLM(
        generation_by_passage={
            PASSAGE_OK: generation_json("Combien de jours de télétravail ?", "Trois jours.")
        },
        judge_response=judge_json(0.9, "Réponse fidèle."),
    )
    monkeypatch.setattr(
        evaluations,
        "_service",
        lambda db, settings: EvaluationService(db, settings, engine=engine, llm=llm),
    )

    try:
        outcome = evaluations.run_async_evaluation(str(kb.knowledge_base_id), str(run_id))

        assert outcome == "completed"
        run = run_sync(worker_sessions, lambda db: db.get(EvaluationRun, run_id))
        assert run is not None
        assert run.status == EvaluationRunStatus.COMPLETED
        assert run.sample_size == 1
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))


def test_a_missing_pending_run_is_a_silent_skip(worker_sessions: SessionFactory) -> None:
    """Course dispatch → exécution : run disparu entre-temps, pas une erreur."""
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db, slug="kb-async-missing", document_external_ids=["doc-a"]
        ),
    )

    try:
        outcome = evaluations.run_async_evaluation(str(kb.knowledge_base_id), str(uuid.uuid4()))

        assert outcome == "skipped"
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))


def test_an_async_run_failure_survives_the_task_level_rollback(
    worker_sessions: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Régression ciblée : le `failed` doit survivre au `ROLLBACK` de `run_in_session`.

    `run_pending_evaluation` (`app.services.evaluations`) commite explicitement
    le statut `failed` avant de relever l'exception ; sans ce commit,
    `run_in_session` (`app.workers.session.session_scope`) ferait un
    `ROLLBACK` sur l'exception qui remonte et annulerait ce `failed` avec le
    reste de la transaction — le run resterait bloqué en `pending` pour
    toujours, invisible comme échoué depuis `GET .../evaluations/{id}`. Ce
    test relit le run depuis une session neuve (`worker_sessions`), exactement
    comme le ferait un client qui interroge l'API après coup — pas depuis la
    session qui a produit l'écriture, qui verrait la mutation même sans commit
    réel.
    """
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db, slug="kb-async-fail", document_external_ids=["doc-fail"]
        ),
    )
    run_id = run_sync(
        worker_sessions, lambda db: _seed_pending_run(db, kb.knowledge_base_id, kb.owner_id)
    )
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={"doc-fail": [EngineChunk(chunk_id="c1", content=PASSAGE_FAIL)]},
    )
    llm = SwitchingLLM(
        generation_by_passage={},
        judge_response=judge_json(0.5, "Sans objet."),
        failing_passages=frozenset({PASSAGE_FAIL}),
    )
    monkeypatch.setattr(
        evaluations,
        "_service",
        lambda db, settings: EvaluationService(db, settings, engine=engine, llm=llm),
    )
    caplog.set_level(logging.ERROR, logger="app.workers.tasks.evaluations")

    try:
        outcome = evaluations.run_async_evaluation(str(kb.knowledge_base_id), str(run_id))

        assert outcome == "failed"
        run = run_sync(worker_sessions, lambda db: db.get(EvaluationRun, run_id))
        assert run is not None
        assert run.status == EvaluationRunStatus.FAILED
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))

    failure_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == evaluations.ASYNC_EVALUATION_FAILURE_MARKER
    ]
    assert len(failure_records) == 1
    assert failure_records[0].knowledge_base_id == str(kb.knowledge_base_id)
    assert failure_records[0].evaluation_run_id == str(run_id)
    assert failure_records[0].error_type == "LLMRateLimitError"


def test_an_async_run_without_a_reconstructible_requester_is_marked_failed(
    worker_sessions: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """Défensif : `requested_by_id` absent (jamais atteint via `POST .../evaluations`,
    qui exige un principal `EDITOR` authentifié) — le run est marqué en échec plutôt que
    de tenter un appel `RetrievalService` sans principal reconstructible."""
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db, slug="kb-async-no-requester", document_external_ids=["doc-a"]
        ),
    )
    run_id = run_sync(worker_sessions, lambda db: _seed_pending_run(db, kb.knowledge_base_id, None))
    caplog.set_level(logging.ERROR, logger="app.workers.tasks.evaluations")

    try:
        outcome = evaluations.run_async_evaluation(str(kb.knowledge_base_id), str(run_id))

        assert outcome == "failed"
        run = run_sync(worker_sessions, lambda db: db.get(EvaluationRun, run_id))
        assert run is not None
        assert run.status == EvaluationRunStatus.FAILED
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))


# ─── Dépense LLM de la routine (ADR-054) ─────────────────────────────────────


def test_bases_owned_by_a_guest_or_a_suspended_account_are_not_dispatched(
    worker_sessions: SessionFactory, eager_celery: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le fan-out quotidien ne vise que des bases de comptes réels actifs : une base d'essai
    ouverte d'un `POST /guest-sessions` ne coûte pas un run par jour."""
    account_kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(db, slug="kb-account", document_external_ids=["doc-a"]),
    )
    guest_kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db, slug="kb-guest", document_external_ids=["doc-b"], owner_is_guest=True
        ),
    )
    suspended_kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db, slug="kb-suspended", document_external_ids=["doc-c"], owner_is_active=False
        ),
    )
    dispatched_ids: list[str] = []
    monkeypatch.setattr(
        evaluations,
        "run_scheduled_evaluation",
        type("_Task", (), {"delay": staticmethod(dispatched_ids.append)}),
    )

    try:
        dispatched = evaluations.dispatch_scheduled_evaluations()

        assert dispatched_ids == [str(account_kb.knowledge_base_id)]
        assert dispatched == 1
    finally:
        for kb in (account_kb, guest_kb, suspended_kb):
            run_sync(worker_sessions, lambda db, owner_id=kb.owner_id: _purge(db, owner_id))


def test_a_scheduled_run_on_a_guest_owned_base_makes_no_llm_call(
    worker_sessions: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Même publiée à la main (ou avant que le propriétaire ne devienne invité), la tâche ne
    dépense rien pour une base d'invité."""
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db, slug="kb-guest-direct", document_external_ids=["doc-a"], owner_is_guest=True
        ),
    )
    llm = CountingLLM(
        generation_by_passage={PASSAGE_OK: generation_json("Question ?", "Réponse.")},
        judge_response=judge_json(0.9, "Correct."),
    )
    engine = FakeIngestionEngine(
        strict=False, chunks_by_document={"doc-a": [EngineChunk(chunk_id="c1", content=PASSAGE_OK)]}
    )
    monkeypatch.setattr(
        evaluations,
        "_service",
        lambda db, settings: EvaluationService(db, settings, engine=engine, llm=llm),
    )

    try:
        assert evaluations.run_scheduled_evaluation(str(kb.knowledge_base_id)) == "completed"
        assert llm.calls == 0
        assert run_sync(worker_sessions, lambda db: _runs_for(db, kb.knowledge_base_id)) == []
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))


def test_a_scheduled_run_is_counted_on_the_owner_account_and_cannot_exceed_it(
    worker_sessions: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La routine ne contourne pas les compteurs : premier run = 3 unités au compte du
    propriétaire, sur le compteur **d'évaluation** (addendum 2026-09-21 à ADR-054, distinct
    de celui du chat) ; plafond de 3 → le run du lendemain (même fenêtre ici) échoue sans
    appel."""
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(db, slug="kb-counted", document_external_ids=["doc-a"]),
    )
    llm = CountingLLM(
        generation_by_passage={PASSAGE_OK: generation_json("Question ?", "Réponse.")},
        judge_response=judge_json(0.9, "Correct."),
    )
    engine = FakeIngestionEngine(
        strict=False, chunks_by_document={"doc-a": [EngineChunk(chunk_id="c1", content=PASSAGE_OK)]}
    )
    monkeypatch.setattr(
        evaluations,
        "_service",
        lambda db, settings: EvaluationService(db, settings, engine=engine, llm=llm),
    )
    settings = Settings(evaluation_scheduled_sample_size=1, platform_llm_evaluation_daily_limit=3)
    monkeypatch.setattr(evaluations, "get_settings", lambda: settings)

    try:
        assert evaluations.run_scheduled_evaluation(str(kb.knowledge_base_id)) == "completed"
        assert llm.calls == 3
        assert asyncio.run(peek_evaluation_quota(settings, kb.owner_id))[0] == 3
        assert asyncio.run(peek_global_budget(settings))[0] == 3

        assert evaluations.run_scheduled_evaluation(str(kb.knowledge_base_id)) == "failed"
        assert llm.calls == 3
        runs = run_sync(worker_sessions, lambda db: _runs_for(db, kb.knowledge_base_id))
        assert len(runs) == 1
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))


def test_a_full_default_scheduled_run_completes_under_the_new_evaluation_quota(
    worker_sessions: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le scénario même du problème signalé (addendum 2026-09-21 à ADR-054) : un run programmé
    de 8 questions (le défaut, `evaluation_scheduled_sample_size`) consomme jusqu'à 24 appels.
    Sous l'ancien plafond de chat partagé (20/jour), il s'arrêtait en route. Sous le nouveau
    plafond d'évaluation dédié (24/jour par défaut), il se termine avec ses 8 items — et le
    plafond de chat du propriétaire, lui, reste intact."""
    kb = run_sync(
        worker_sessions,
        lambda db: _seed_evaluation_kb(
            db,
            slug="kb-full-scheduled-run",
            document_external_ids=[f"doc-{index}" for index in range(8)],
        ),
    )
    llm = CountingLLM(
        generation_by_passage={PASSAGE_OK: generation_json("Question ?", "Réponse.")},
        judge_response=judge_json(0.9, "Correct."),
    )
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={
            f"doc-{index}": [EngineChunk(chunk_id=f"c{index}", content=PASSAGE_OK)]
            for index in range(8)
        },
    )
    monkeypatch.setattr(
        evaluations,
        "_service",
        lambda db, settings: EvaluationService(db, settings, engine=engine, llm=llm),
    )
    settings = Settings()
    assert settings.evaluation_scheduled_sample_size == 8
    assert settings.platform_llm_evaluation_daily_limit == 24
    monkeypatch.setattr(evaluations, "get_settings", lambda: settings)

    try:
        assert evaluations.run_scheduled_evaluation(str(kb.knowledge_base_id)) == "completed"
        assert llm.calls == 24
        runs = run_sync(worker_sessions, lambda db: _runs_for(db, kb.knowledge_base_id))
        assert len(runs) == 1
        assert asyncio.run(peek_evaluation_quota(settings, kb.owner_id))[0] == 24
        # Le plafond de chat du propriétaire (20/jour par défaut) n'a pas été entamé.
        assert asyncio.run(peek_user_quota(settings, kb.owner_id))[0] == 0
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, kb.owner_id))


@dataclass(slots=True)
class CountingLLM:
    """`LLMPort` scripté qui compte chaque appel émis (`complete` et `stream`)."""

    generation_by_passage: dict[str, str]
    judge_response: str
    calls: int = 0
    capabilities: LLMCapabilities = field(default_factory=lambda: _capabilities("scripted-llm"))

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        self.calls += 1
        user_content = messages[-1].content
        if user_content.startswith("Question : "):
            return self.judge_response
        return self.generation_by_passage[user_content]

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        self.calls += 1
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        yield "réponse générée par le chat"
