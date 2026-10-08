"""Balayage de maintenance : jobs orphelins, décisions expirées, invités échus (ADR-055).

Trois défauts partageaient la même cause — l'absence de tout balayage
périodique — et se testent donc ensemble ici, chacun dans sa propre section :

1. Un job `embedding`/`indexing` dont la chaîne de sondage a disparu doit être
   fait échouer par exactement la même règle que la veille anti-blocage de
   `_poll` ; un job encore activement sondé ne doit jamais être touché.
2. Un job `awaiting_review` sans décision humaine au-delà du délai configuré
   doit reprendre le pipeline avec le profil par défaut ; en-deçà du délai, il
   reste intact.
3. Un espace invité échu doit être nettoyé via les vrais services moteur et
   stockage, jamais par un `DELETE` direct ; un échec moteur laisse la trace
   de ce qu'il reste à faire (rien n'est supprimé) pour le passage suivant.

Comme le reste de `tests/workers/`, ces tests sont synchrones et s'appuient
sur un jeu de données réellement commité (`worker_sessions`) : les tâches
Celery ouvrent leur propre session, une fixture de transaction non commitée ne
leur serait pas visible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import IngestionJob
from app.domain.enums import DocumentStatus, JobState
from app.providers.engine.fake import FakeIngestionEngine
from app.workers.celery_app import EMBEDDING_TASK
from app.workers.tasks import maintenance
from tests.workers.conftest import (
    FakeStorage,
    Publications,
    SeededJob,
    SessionFactory,
    advance_to,
    age_last_observation,
    document_of,
    document_status_of,
    job_of,
    link_engine_refs,
    run_sync,
    state_of,
)

pytestmark = pytest.mark.usefixtures("worker_sessions")


def age_updated_at(factory: SessionFactory, job_id: uuid.UUID, *, hours: int) -> None:
    """Recule `IngestionJob.updated_at` par un `UPDATE` Core, hors ORM.

    `onupdate=NOW` (`app.db.base.TimestampMixin`) ne s'applique qu'aux
    écritures issues du *flush* de l'unité de travail ORM ; un `UPDATE` Core
    explicite n'en déclenche aucun et pose exactement la valeur demandée —
    seul moyen de simuler qu'un job est resté `awaiting_review` depuis
    longtemps sans dépendre de l'horloge réelle du test.
    """

    async def _apply(db: AsyncSession) -> None:
        await db.execute(
            update(IngestionJob)
            .where(IngestionJob.id == job_id)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=hours))
        )

    run_sync(factory, _apply)


# ─── Item 1 : jobs d'ingestion orphelins ──────────────────────────────────────


@pytest.fixture
def embedding_job(worker_sessions: SessionFactory, queued_job: SeededJob) -> SeededJob:
    """Job arrivé en `embedding`, avec les références que le moteur lui connaît."""
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


def _orphan_idle_seconds(*, margin: int) -> int:
    settings = get_settings()
    return (
        settings.ragflow_stuck_job_timeout_seconds
        + settings.ingestion_job_orphan_grace_seconds
        + margin
    )


def test_a_job_whose_polling_chain_died_is_swept_and_fails(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
) -> None:
    """Immobile depuis bien plus que le seuil élargi : personne ne le sonde plus, il échoue."""
    age_last_observation(
        worker_sessions, embedding_job.job_id, seconds=_orphan_idle_seconds(margin=60)
    )

    outcome = maintenance.sweep_stalled_ingestion_job(embedding_job.argument)

    assert outcome == "failed"
    assert fake_engine.cancellations == 1
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.FAILED
    job = job_of(worker_sessions, embedding_job.job_id)
    assert job.error is not None
    assert job.error["code"] == "engine_stalled"
    assert job.error["retryable"] is False


def test_a_job_whose_polling_is_still_recent_is_left_alone(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
) -> None:
    """Une chaîne de sondage vivante n'est jamais réveillée par le balayage."""
    age_last_observation(worker_sessions, embedding_job.job_id, seconds=5)

    outcome = maintenance.sweep_stalled_ingestion_job(embedding_job.argument)

    assert outcome == "skipped"
    assert fake_engine.cancellations == 0
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.EMBEDDING


def test_a_job_within_the_poll_timeout_alone_is_not_yet_orphaned(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
) -> None:
    """Au-delà du seuil de `_poll` mais pas de la marge élargie : une chaîne vivante l'aurait
    déjà elle-même fait échouer avant que ce délai ne soit atteint — pas encore un orphelin."""
    settings = get_settings()
    age_last_observation(
        worker_sessions,
        embedding_job.job_id,
        seconds=settings.ragflow_stuck_job_timeout_seconds + 60,
    )

    outcome = maintenance.sweep_stalled_ingestion_job(embedding_job.argument)

    assert outcome == "skipped"
    assert fake_engine.cancellations == 0
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.EMBEDDING


def test_a_job_that_progressed_meanwhile_is_left_alone(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
) -> None:
    """Une chaîne concurrente a fait avancer le job entre la sélection et ce passage : on
    renonce."""
    age_last_observation(
        worker_sessions, embedding_job.job_id, seconds=_orphan_idle_seconds(margin=60)
    )
    advance_to(worker_sessions, embedding_job.job_id, JobState.INDEXING)
    # Une chaîne de sondage réellement vivante rafraîchit toujours
    # `engine_progress_observed_at` dans la même écriture que la transition
    # (`_observe`, appelé par `_poll` avant tout dispatch de successeur,
    # `app.workers.tasks.ingestion`) : `advance_to` saute ce témoin puisqu'il
    # passe directement par le service de transition. On le simule ici pour
    # représenter fidèlement une chaîne vivante, plutôt qu'un état que la
    # production ne produit jamais (transition récente, témoin resté ancien).
    age_last_observation(worker_sessions, embedding_job.job_id, seconds=0)

    outcome = maintenance.sweep_stalled_ingestion_job(embedding_job.argument)

    assert outcome == "skipped"
    assert fake_engine.cancellations == 0
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.INDEXING


def test_the_indexing_step_is_also_covered(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
) -> None:
    """Un blocage peut survenir après la bascule vers `indexing` : le balayage la couvre aussi."""
    advance_to(worker_sessions, embedding_job.job_id, JobState.INDEXING)
    age_last_observation(
        worker_sessions, embedding_job.job_id, seconds=_orphan_idle_seconds(margin=60)
    )

    outcome = maintenance.sweep_stalled_ingestion_job(embedding_job.argument)

    assert outcome == "failed"
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.FAILED


def test_two_sweep_passes_on_the_same_orphan_are_idempotent(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
) -> None:
    """Un job déjà échoué au premier passage ne l'est pas une seconde fois."""
    age_last_observation(
        worker_sessions, embedding_job.job_id, seconds=_orphan_idle_seconds(margin=60)
    )

    first = maintenance.sweep_stalled_ingestion_job(embedding_job.argument)
    second = maintenance.sweep_stalled_ingestion_job(embedding_job.argument)

    assert (first, second) == ("failed", "skipped")
    assert fake_engine.cancellations == 1


# ─── Item 2 : décisions de precheck expirées ─────────────────────────────────


@pytest.fixture
def awaiting_review_job(worker_sessions: SessionFactory, queued_job: SeededJob) -> SeededJob:
    """Job arrivé en `awaiting_review`, sans décision — l'état candidat de ce balayage."""
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)
    return queued_job


def test_an_expired_awaiting_review_job_resumes_with_the_default_profile(
    worker_sessions: SessionFactory,
    awaiting_review_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
    eager_celery: None,
) -> None:
    """Au-delà du délai configuré, la même arête que la décision explicite est empruntée."""
    settings = get_settings()
    age_updated_at(
        worker_sessions,
        awaiting_review_job.job_id,
        hours=settings.awaiting_review_decision_timeout_hours + 1,
    )

    outcome = maintenance.resume_expired_precheck_decision(awaiting_review_job.argument)

    assert outcome == "resumed"
    assert state_of(worker_sessions, awaiting_review_job.job_id) is JobState.CHUNKING
    assert (
        document_status_of(worker_sessions, awaiting_review_job.document_id)
        is DocumentStatus.PROCESSING
    )
    document = document_of(worker_sessions, awaiting_review_job.document_id)
    assert document.precheck_decision is None  # profil par défaut, jamais écrit ici
    assert publications.successors == [EMBEDDING_TASK]


def test_an_awaiting_review_job_not_yet_expired_is_left_alone(
    worker_sessions: SessionFactory,
    awaiting_review_job: SeededJob,
    publications: Publications,
    eager_celery: None,
) -> None:
    """En-deçà du délai, aucune décision n'est prise à la place de l'utilisateur."""
    outcome = maintenance.resume_expired_precheck_decision(awaiting_review_job.argument)

    assert outcome == "skipped"
    assert state_of(worker_sessions, awaiting_review_job.job_id) is JobState.AWAITING_REVIEW
    assert publications.successors == []


def test_two_resume_passes_on_the_same_job_are_idempotent(
    worker_sessions: SessionFactory,
    awaiting_review_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
    eager_celery: None,
) -> None:
    """Une décision déjà reprise ne republie pas une seconde fois `run_chunking`."""
    settings = get_settings()
    age_updated_at(
        worker_sessions,
        awaiting_review_job.job_id,
        hours=settings.awaiting_review_decision_timeout_hours + 1,
    )

    first = maintenance.resume_expired_precheck_decision(awaiting_review_job.argument)
    second = maintenance.resume_expired_precheck_decision(awaiting_review_job.argument)

    assert (first, second) == ("resumed", "skipped")
    assert publications.successors == [EMBEDDING_TASK]


# ─── Routage `beat_schedule` ──────────────────────────────────────────────────


def test_the_beat_entry_is_routed_on_the_default_queue() -> None:
    """CONTRIBUTING.md §2 : aucune tâche de maintenance ne doit jamais atterrir sur `gpu`."""
    from app.workers.celery_app import (
        DEFAULT_QUEUE,
        RESUME_EXPIRED_PRECHECK_DECISION_TASK,
        RUN_MAINTENANCE_SWEEP_TASK,
        SWEEP_STALLED_INGESTION_JOB_TASK,
        TASK_ROUTES,
    )
    from app.workers.celery_app import (
        app as celery_app,
    )

    for task_name in (
        RUN_MAINTENANCE_SWEEP_TASK,
        SWEEP_STALLED_INGESTION_JOB_TASK,
        RESUME_EXPIRED_PRECHECK_DECISION_TASK,
    ):
        assert TASK_ROUTES[task_name] == {"queue": DEFAULT_QUEUE}

    assert "maintenance-sweep" in celery_app.conf.beat_schedule
    assert celery_app.conf.beat_schedule["maintenance-sweep"]["task"] == RUN_MAINTENANCE_SWEEP_TASK


# ─── Dispatch : sélection bornée, verrou, fan-out ────────────────────────────


def _stub_task(collector: list[str]) -> type:
    """Double minimal d'une tâche Celery : seul `.delay` compte pour ces tests.

    Toutes les catégories sont stubbées (celles des greffons par `task_by_name`)
    dans les deux tests qui suivent, pas seulement celle qu'ils examinent : sans
    ça, un candidat réel des deux
    *autres* catégories — laissé par une exécution précédente de la suite
    contre la même base de test partagée — ferait republier une vraie tâche
    Celery, hors du périmètre de ce test et sans rapport avec ce qu'il vérifie.
    """
    return type("_Task", (), {"delay": staticmethod(collector.append)})


def test_dispatch_fans_out_one_task_per_stalled_job(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le point d'entrée `beat` ne traite rien lui-même : il sélectionne puis publie."""
    age_last_observation(
        worker_sessions, embedding_job.job_id, seconds=_orphan_idle_seconds(margin=60)
    )
    dispatched: list[str] = []
    monkeypatch.setattr(maintenance, "sweep_stalled_ingestion_job", _stub_task(dispatched))
    monkeypatch.setattr(maintenance, "resume_expired_precheck_decision", _stub_task([]))
    monkeypatch.setattr(maintenance, "task_by_name", lambda name: _stub_task([]))

    counts = maintenance.run_maintenance_sweep()

    assert counts["stalled_ingestion_jobs"] == 1
    assert dispatched == [str(embedding_job.job_id)]
    # Rien n'a été décidé par le dispatch lui-même : l'état reste inchangé.
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.EMBEDDING


def test_a_concurrent_dispatch_skips_instead_of_duplicating(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le verrou consultatif non bloquant : un second passage renonce, il n'attend jamais."""
    age_last_observation(
        worker_sessions, embedding_job.job_id, seconds=_orphan_idle_seconds(margin=60)
    )
    dispatched: list[str] = []
    monkeypatch.setattr(maintenance, "sweep_stalled_ingestion_job", _stub_task(dispatched))
    monkeypatch.setattr(maintenance, "resume_expired_precheck_decision", _stub_task([]))
    monkeypatch.setattr(maintenance, "task_by_name", lambda name: _stub_task([]))
    monkeypatch.setattr(maintenance, "_try_lock", lambda db: _false(db))

    counts = maintenance.run_maintenance_sweep()

    assert counts == {"skipped": 1}
    assert dispatched == []


async def _false(db: AsyncSession) -> bool:
    del db
    return False
