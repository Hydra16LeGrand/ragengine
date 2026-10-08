"""Garde-fou qualité d'extraction (Phase 11 Étape 4, ADR-024) : câblage pipeline.

Deux points d'écriture peuvent conclure `JobState.COMPLETED` pour l'étape
d'indexation :

- `_poll`, le cas rapide où l'entrée et la conclusion coïncident sur le même
  sondage (`entering and finished`) ;
- `_complete()`, le cas normal où l'entrée a déjà été écrite lors d'un sondage
  précédent (chemin `RESUMED`).

L'ADR-024 identifie explicitement le risque de « couture oubliée » entre ces
deux points — la même classe de bug que le dispatch Celery manquant de la
Phase 4 (plan interne). Ces tests vérifient qu'aucun des deux ne peut
écrire `completed` sans que le calcul qualité ait eu lieu, en rejouant le même
scénario (0 chunk) par les deux chemins et en comparant le résultat.

La logique de seuils elle-même (policy ADR-024 §3 : exclusion des documents
courts, métriques `None` ignorées…) est testée unitairement dans
`tests/ingestion/test_quality.py` — ici, seul le câblage pipeline compte :
quand le calcul a lieu, ce qu'il écrit, et qu'il ne bloque jamais.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document
from app.domain.enums import DocumentStatus, JobState
from app.providers.engine.base import EngineChunk
from app.providers.engine.fake import FakeIngestionEngine
from app.workers.tasks.ingestion import Outcome, run_indexing, run_precheck
from tests.workers.conftest import (
    DEFAULT_CHUNK_TEXT,
    DONE,
    FakeStorage,
    Publications,
    SeededJob,
    SessionFactory,
    advance_to,
    document_of,
    document_status_of,
    job_of,
    link_engine_refs,
    parse_status,
    run_sync,
    state_of,
)

INDEXING_IN_PROGRESS = parse_status(
    "running", inferred_phase=JobState.INDEXING, progress=0.9, last_message="Indexing..."
)


def set_content_type(factory: SessionFactory, document_id: uuid.UUID, content_type: str) -> None:
    """Change le format déclaré d'un document — pour tester le comportement non-PDF."""

    async def _apply(db: AsyncSession) -> None:
        document = await db.get(Document, document_id)
        assert document is not None
        document.content_type = content_type

    run_sync(factory, _apply)


def set_precheck_decision(
    factory: SessionFactory, document_id: uuid.UUID, decision: dict[str, bool]
) -> None:
    """Pose `Document.precheck_decision` — pour tester l'interaction `use_parent_child`
    avec le garde-fou qualité (Phase 13)."""

    async def _apply(db: AsyncSession) -> None:
        document = await db.get(Document, document_id)
        assert document is not None
        document.precheck_decision = decision

    run_sync(factory, _apply)


@pytest.fixture
def embedding_job(worker_sessions: SessionFactory, queued_job: SeededJob) -> SeededJob:
    """Job arrivé en `embedding` : la prochaine bascule est celle de l'indexation."""
    advance_to(
        worker_sessions,
        queued_job.job_id,
        JobState.PRECHECKING,
        JobState.AWAITING_REVIEW,
        JobState.CHUNKING,
        JobState.EMBEDDING,
    )
    link_engine_refs(worker_sessions, queued_job)
    return queued_job


# ─── Chemin sain : aucun avertissement ────────────────────────────────────────


def test_a_healthy_document_completes_without_a_warning(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """Le garde-fou ne doit pas être bruyant sur un cas propre (DoD, plan interne).

    `fake_engine.chunks` par défaut porte le même texte que `fake_storage`
    (`DEFAULT_CHUNK_TEXT`, cf. `tests/workers/conftest.py`) : bonne couverture,
    aucun fragment dégénéré.
    """
    fake_engine.statuses = [DONE]

    outcome = run_indexing(embedding_job.argument)

    assert outcome == Outcome.EXECUTED
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.COMPLETED
    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is None
    document = document_of(worker_sessions, embedding_job.document_id)
    assert document.has_quality_warning is False
    # Compte de fragments (Phase 13 bis) : même lecture moteur que le garde-fou,
    # persisté même sur un job sans avertissement.
    assert fake_engine.chunks is not None
    assert document.chunk_count == len(fake_engine.chunks)


# ─── Compte de fragments : même geste que le garde-fou (Phase 13 bis) ─────────


def test_chunk_count_is_persisted_via_the_fast_completion_path(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """`page.document.chunk_count` (total réel, non borné par l'échantillon qualité)
    est persisté sur `Document.chunk_count` par le chemin rapide (`_poll`)."""
    fake_engine.chunks = [
        EngineChunk(chunk_id=f"chunk-{index}", content=DEFAULT_CHUNK_TEXT) for index in range(5)
    ]
    fake_engine.statuses = [DONE]

    outcome = run_indexing(embedding_job.argument)

    assert outcome == Outcome.EXECUTED
    document = document_of(worker_sessions, embedding_job.document_id)
    assert document.chunk_count == 5


def test_chunk_count_is_persisted_via_the_resumed_completion_path(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Même vérification par l'autre point d'écriture (`_complete()`) — le même
    verrou anti-« couture oubliée » que pour `warning`, appliqué à `chunk_count`.
    """
    fake_engine.chunks = [
        EngineChunk(chunk_id=f"chunk-{index}", content=DEFAULT_CHUNK_TEXT) for index in range(5)
    ]
    fake_engine.statuses = [INDEXING_IN_PROGRESS]
    run_indexing(embedding_job.argument)  # écrit l'entrée `indexing`, rien de plus
    fake_engine.statuses = [DONE]

    outcome = run_indexing(embedding_job.argument)  # sonde la conclusion via `_complete()`

    assert outcome == Outcome.RESUMED
    document = document_of(worker_sessions, embedding_job.document_id)
    assert document.chunk_count == 5


# ─── 0 chunk : signal univoque, tout format ───────────────────────────────────


def test_zero_chunks_triggers_a_warning_via_the_fast_completion_path(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """Cas rapide : entrée et conclusion sur le même sondage (`_poll`, branche `finished`)."""
    fake_engine.chunks = []
    fake_engine.statuses = [DONE]

    outcome = run_indexing(embedding_job.argument)

    assert outcome == Outcome.EXECUTED
    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is not None
    assert job.warning["code"] == "zero_chunks"
    document = document_of(worker_sessions, embedding_job.document_id)
    assert document.has_quality_warning is True
    # L'avertissement est orthogonal à l'état du pipeline (ADR-024) : le
    # document reste `indexed`, `DocumentStatus` n'a pas de nouvelle valeur.
    assert document_status_of(worker_sessions, embedding_job.document_id) is DocumentStatus.INDEXED


def test_zero_chunks_triggers_a_warning_via_the_resumed_completion_path(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Cas normal : la bascule d'entrée est déjà écrite, seule la conclusion reste (`_complete`).

    Même scénario (0 chunk) que le chemin rapide ci-dessus, par l'autre point
    d'écriture — c'est le verrou anti-régression contre la « couture oubliée »
    identifiée par l'ADR-024 : si `_complete()` cessait d'appeler le calcul
    qualité, ce test serait le seul à le remarquer (le chemin rapide, testé
    séparément, resterait vert).

    `publications` intercepte `_reschedule` : le premier sondage (moteur pas
    encore fini) le déclenche pour de vrai sans elle, ce qui tenterait de
    publier sur le broker Celery réel (plan interne, même piège que les
    tests de sondage existants, `tests/workers/test_ingestion_polling.py`).
    """
    fake_engine.chunks = []
    fake_engine.statuses = [INDEXING_IN_PROGRESS]
    run_indexing(embedding_job.argument)  # écrit l'entrée `indexing`, rien de plus
    fake_engine.statuses = [DONE]

    outcome = run_indexing(embedding_job.argument)  # sonde la conclusion via `_complete()`

    assert outcome == Outcome.RESUMED
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.COMPLETED
    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is not None
    assert job.warning["code"] == "zero_chunks"
    document = document_of(worker_sessions, embedding_job.document_id)
    assert document.has_quality_warning is True


def test_zero_chunks_triggers_regardless_of_document_format(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """ADR-024 §3 : 0 chunk avertit « quel que soit le format », pas seulement le PDF."""
    set_content_type(worker_sessions, embedding_job.document_id, "text/csv")
    fake_engine.chunks = []
    fake_engine.statuses = [DONE]

    run_indexing(embedding_job.argument)

    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is not None
    assert job.warning["code"] == "zero_chunks"


# ─── Contenu dégénéré ──────────────────────────────────────────────────────────


def test_degenerate_content_triggers_a_warning_end_to_end(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """Une majorité de fragments minuscules, sur un document par ailleurs substantiel."""
    fake_engine.chunks = [
        EngineChunk(chunk_id=f"degenerate-{index}", content="Q") for index in range(10)
    ] + [EngineChunk(chunk_id="substantial", content=DEFAULT_CHUNK_TEXT)]
    fake_engine.statuses = [DONE]

    run_indexing(embedding_job.argument)

    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is not None
    assert job.warning["code"] == "degenerate_content"
    assert job.warning["metrics"]["degenerate_chunk_pct"] == pytest.approx(10 / 11)


def test_degenerate_content_is_silenced_when_parent_child_was_active_end_to_end(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """`use_parent_child` (Phase 13) : mêmes fragments dégénérés que le test ci-dessus,
    mais silencieux une fois l'option posée sur `Document.precheck_decision`.

    `list_document_chunks` mélange, une fois `parent_child` actif, des
    fragments enfants courts par construction et des fragments « mère » plus
    longs (fait vérifié en test de bout en bout, Phase 13) — le seuil
    `degenerate_content` n'a plus de sens pour ce document, cf.
    `quality.py::evaluate_quality`.
    """
    set_precheck_decision(worker_sessions, embedding_job.document_id, {"use_parent_child": True})
    fake_engine.chunks = [
        EngineChunk(chunk_id=f"degenerate-{index}", content="Q") for index in range(10)
    ] + [EngineChunk(chunk_id="substantial", content=DEFAULT_CHUNK_TEXT)]
    fake_engine.statuses = [DONE]

    run_indexing(embedding_job.argument)

    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is None
    document = document_of(worker_sessions, embedding_job.document_id)
    assert document.has_quality_warning is False


def test_low_source_coverage_still_triggers_when_parent_child_was_active_end_to_end(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """`use_parent_child` neutralise `degenerate_content`, pas `low_source_coverage`.

    Le texte total retrouvé reste un signal fiable indépendamment de la taille
    des fragments — même scénario que `test_low_source_coverage_triggers_a_
    warning_end_to_end`, avec l'option active en plus.
    """
    set_precheck_decision(worker_sessions, embedding_job.document_id, {"use_parent_child": True})
    fake_engine.chunks = [EngineChunk(chunk_id="unrelated", content="x" * 200)]
    fake_engine.statuses = [DONE]

    run_indexing(embedding_job.argument)

    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is not None
    assert job.warning["code"] == "low_source_coverage"


# ─── Couverture source (PDF uniquement) ────────────────────────────────────────


def test_low_source_coverage_triggers_a_warning_end_to_end(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """Fragments longs (non dégénérés) mais sans rapport avec le texte source du PDF."""
    fake_engine.chunks = [EngineChunk(chunk_id="unrelated", content="x" * 200)]
    fake_engine.statuses = [DONE]

    run_indexing(embedding_job.argument)

    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is not None
    assert job.warning["code"] == "low_source_coverage"


def test_a_non_pdf_document_never_gets_a_source_coverage_warning(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """Hors périmètre v1 (ADR-024 §3) : pas de référence indépendante pour le non-PDF.

    Un contenu qui déclencherait `low_source_coverage` sur un PDF (cf. test
    ci-dessus) ne doit rien déclencher ici — et le fichier source ne doit même
    pas être relu, `source_coverage` n'ayant de sens que pour un PDF.
    """
    set_content_type(worker_sessions, embedding_job.document_id, "text/csv")
    fake_engine.chunks = [EngineChunk(chunk_id="unrelated", content="x" * 200)]
    fake_engine.statuses = [DONE]

    run_indexing(embedding_job.argument)

    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is None
    assert fake_storage.reads == []


# ─── Le garde-fou ne bloque jamais (ADR-024) ───────────────────────────────────


def test_a_quality_computation_failure_never_blocks_completion(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un moteur injoignable au moment du calcul qualité ne doit pas faire échouer le job.

    Le job a par ailleurs réussi techniquement (le moteur a bien répondu
    `done` au sondage) : un accroc du calcul qualité — ici simulé sur
    `list_document_chunks` — se traduit par « pas d'avertissement »,
    journalisé, jamais par un job en échec.

    La panne se scénarise par `fake_engine.failures` (option du moteur factice).
    """

    fake_engine.failures["list_document_chunks"] = RuntimeError("moteur injoignable")
    fake_engine.statuses = [DONE]

    outcome = run_indexing(embedding_job.argument)

    assert outcome == Outcome.EXECUTED
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.COMPLETED
    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.warning is None
    document = document_of(worker_sessions, embedding_job.document_id)
    assert document.has_quality_warning is False
    assert document.chunk_count is None


# ─── Aucun autre point de couture ne calcule quoi que ce soit ─────────────────


def test_the_precheck_completion_never_computes_quality(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """`_complete()` est partagé avec `PRECHECK_STEP` (conclusion vers `awaiting_review`) —

    le garde de `step.completion is not JobState.COMPLETED` doit l'empêcher d'y
    calculer quoi que ce soit : aucun appel moteur, aucune lecture de stockage
    au-delà de ce que le precheck lui-même effectue.
    """
    fake_storage.reads = []

    run_precheck(queued_job.argument)

    assert fake_engine.chunk_list_calls == []
