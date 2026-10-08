"""Cycle de vie d'un job d'ingestion : ouverture, transitions, annulation.

Ce que ces tests verrouillent : la transition écrit **dans la même transaction**
l'état du job, la projection `Document.status` et le journal d'audit
(`docs/domain-model.md` §2 et §3). Une transition illégale ne touche à rien et
remonte en `409`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.db.models import Document, IngestionJob, KnowledgeBase, User
from app.domain.enums import DocumentStatus, JobState, MemberRole, Visibility
from app.domain.schemas.ingestion_job import JobError
from app.providers.storage import LocalFilesystemStorageAdapter, StoragePort
from app.services.identity.principal import ANONYMOUS
from app.services.jobs import IngestionJobService
from app.services.knowledge_bases import KnowledgeBaseService
from app.workers.celery_app import CHUNKING_TASK, PRECHECK_TASK
from tests.conftest import DispatchedTasks
from tests.services.test_documents import documents_of, ingestion
from tests.services.test_knowledge_bases import access_of, account, creation, principal_of


@dataclass(frozen=True, slots=True)
class Fixture:
    """Base, document et job fraîchement ouverts, dans l'état nominal `queued`."""

    owner: User
    knowledge_base: KnowledgeBase
    document: Document
    job: IngestionJob


async def queued_job(
    db: AsyncSession, store: StoragePort, *, visibility: Visibility = Visibility.PRIVATE
) -> Fixture:
    """Ouvre une base, y enregistre un document, et renvoie le job qui en découle."""
    owner = await account(db)
    knowledge_base = await KnowledgeBaseService(db).create(owner, creation(visibility=visibility))
    registered = await documents_of(db, store).register(
        access_of(knowledge_base, owner), ingestion()
    )
    assert registered.document.current_job_id is not None
    job = await IngestionJobService(db).get(registered.document.current_job_id)
    return Fixture(
        owner=owner, knowledge_base=knowledge_base, document=registered.document, job=job
    )


async def events_of(db: AsyncSession, job: IngestionJob) -> list[JobState]:
    """Journal d'audit du job, dans l'ordre chronologique."""
    reloaded = (
        await db.execute(
            select(IngestionJob)
            .options(selectinload(IngestionJob.events))
            .where(IngestionJob.id == job.id)
        )
    ).scalar_one()
    return [event.state for event in reloaded.events]


async def test_opening_a_job_journals_its_first_state(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Le journal d'audit commence dès la mise en file."""
    fixture = await queued_job(db_session, storage)

    assert await events_of(db_session, fixture.job) == [JobState.QUEUED]


async def test_a_legal_transition_projects_the_document_status(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """`prechecking` projette `processing` sur le document, dans la même transaction."""
    fixture = await queued_job(db_session, storage)

    await IngestionJobService(db_session).transition(fixture.job, JobState.PRECHECKING)

    reloaded = await db_session.get(Document, fixture.document.id)
    assert reloaded is not None
    assert reloaded.status is DocumentStatus.PROCESSING
    assert fixture.job.started_at is not None
    assert fixture.job.finished_at is None


async def test_an_illegal_transition_answers_conflict(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Sauter de `queued` à `completed` est refusé, et ne touche à rien."""
    fixture = await queued_job(db_session, storage)

    with pytest.raises(ConflictError) as raised:
        await IngestionJobService(db_session).transition(fixture.job, JobState.COMPLETED)

    assert raised.value.extensions["current_state"] == "queued"
    assert raised.value.extensions["requested_state"] == "completed"
    reloaded = await db_session.get(Document, fixture.document.id)
    assert reloaded is not None
    assert reloaded.status is DocumentStatus.PENDING
    assert await events_of(db_session, fixture.job) == [JobState.QUEUED]


async def test_cancelling_a_queued_job_makes_the_document_ingestible_again(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """`cancelled` projette `DocumentStatus.CANCELLED` — distinct de `pending`, mais le
    document reste ré-ingérable (§3) : rien ne le distingue d'un document neuf pour
    `POST .../documents/ingest`, qui ne branche sur aucun statut."""
    fixture = await queued_job(db_session, storage)

    cancelled = await IngestionJobService(db_session).cancel(fixture.job)

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.finished_at is not None
    reloaded = await db_session.get(Document, fixture.document.id)
    assert reloaded is not None
    assert reloaded.status is DocumentStatus.CANCELLED


async def test_cancelling_journals_the_transition(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Le journal porte la trace de l'annulation, pas seulement l'état final."""
    fixture = await queued_job(db_session, storage)

    await IngestionJobService(db_session).cancel(fixture.job)

    assert await events_of(db_session, fixture.job) == [JobState.QUEUED, JobState.CANCELLED]


async def test_cancelling_an_awaiting_review_job_is_legal(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """L'autre état annulable de la machine : l'attente de décision humaine."""
    fixture = await queued_job(db_session, storage)
    service = IngestionJobService(db_session)
    await service.transition(fixture.job, JobState.PRECHECKING)
    await service.transition(fixture.job, JobState.AWAITING_REVIEW)

    cancelled = await service.cancel(fixture.job)

    assert cancelled.state is JobState.CANCELLED


async def test_cancelling_an_indexing_job_answers_conflict(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Interrompre une indexation en cours laisserait un index à moitié écrit."""
    fixture = await queued_job(db_session, storage)
    service = IngestionJobService(db_session)
    for state in (
        JobState.PRECHECKING,
        JobState.AWAITING_REVIEW,
        JobState.CHUNKING,
        JobState.EMBEDDING,
        JobState.INDEXING,
    ):
        await service.transition(fixture.job, state)

    with pytest.raises(ConflictError):
        await service.cancel(fixture.job)


async def test_cancelling_twice_answers_conflict(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """`cancelled` est terminal : il n'a aucun successeur."""
    fixture = await queued_job(db_session, storage)
    service = IngestionJobService(db_session)
    await service.cancel(fixture.job)

    with pytest.raises(ConflictError):
        await service.cancel(fixture.job)


async def test_listing_the_jobs_of_a_base(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """La colonne dénormalisée évite la jointure sur `documents` (§3)."""
    fixture = await queued_job(db_session, storage)
    await documents_of(db_session, storage).register(
        access_of(fixture.knowledge_base, fixture.owner), ingestion("second.pdf")
    )

    rows = await IngestionJobService(db_session).list_for_knowledge_base(fixture.knowledge_base)

    assert len(rows) == 2
    assert fixture.job.id in {row.id for row in rows}


async def test_an_unknown_job_answers_not_found(db_session: AsyncSession) -> None:
    """Un identifiant inventé ne produit pas de 500."""
    with pytest.raises(NotFoundError):
        await IngestionJobService(db_session).get(uuid.uuid4())


async def test_a_job_of_a_private_base_is_invisible(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """`404` : la visibilité d'un job est celle de sa base de connaissances."""
    fixture = await queued_job(db_session, storage)

    with pytest.raises(NotFoundError):
        await IngestionJobService(db_session).resolve_access(
            ANONYMOUS, fixture.job.id, minimum=MemberRole.VIEWER
        )


async def test_a_job_of_a_public_base_is_readable(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Une base `public` ouvre le suivi de ses jobs (ADR-005)."""
    fixture = await queued_job(db_session, storage, visibility=Visibility.PUBLIC)

    resolved = await IngestionJobService(db_session).resolve_access(
        principal_of(fixture.owner), fixture.job.id, minimum=MemberRole.VIEWER
    )

    assert resolved.job.id == fixture.job.id
    assert resolved.knowledge_base.id == fixture.knowledge_base.id


# ─── Reprise manuelle (`POST /api/v1/jobs/{job_id}/retry`, ADR-020) ──────────


async def failed_job(
    db: AsyncSession,
    store: StoragePort,
    *,
    retry_state: JobState = JobState.QUEUED,
    retryable: bool = True,
    error: JobError | None = None,
) -> Fixture:
    """Un job `failed`, avec une cause structurée exploitable par `retry`."""
    fixture = await queued_job(db, store)
    cause = error or JobError(
        code="engine_transient",
        message="Panne réseau.",
        retryable=retryable,
        retry_state=retry_state,
    )
    failed = await IngestionJobService(db).transition(fixture.job, JobState.FAILED, error=cause)
    return Fixture(
        owner=fixture.owner,
        knowledge_base=fixture.knowledge_base,
        document=fixture.document,
        job=failed,
    )


async def test_retrying_transitions_back_to_the_recorded_source_state(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """Le job retransite vers `error.retry_state`, pas vers un point de départ arbitraire."""
    fixture = await failed_job(db_session, storage, retry_state=JobState.QUEUED)

    retried = await IngestionJobService(db_session).retry(fixture.job)

    assert retried.state is JobState.QUEUED
    reloaded = await db_session.get(Document, fixture.document.id)
    assert reloaded is not None
    assert reloaded.status is DocumentStatus.PENDING


async def test_retrying_clears_the_recorded_cause(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """Une fois repris, le job ne doit plus afficher une cause d'échec périmée."""
    fixture = await failed_job(db_session, storage)

    retried = await IngestionJobService(db_session).retry(fixture.job)

    assert retried.error is None


async def test_retrying_increments_the_attempts_counter(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """`attempts` compte les reprises manuelles — jamais écrit ailleurs."""
    fixture = await failed_job(db_session, storage)
    assert fixture.job.attempts == 0

    retried = await IngestionJobService(db_session).retry(fixture.job)

    assert retried.attempts == 1


async def test_retrying_republishes_the_task_owning_the_source_state(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """`queued` republie `run_precheck` — même correspondance que le pipeline nominal."""
    fixture = await failed_job(db_session, storage, retry_state=JobState.QUEUED)
    dispatched_tasks.calls.clear()  # l'ouverture du job a déjà publié son propre `run_precheck`

    retried = await IngestionJobService(db_session).retry(fixture.job)

    assert dispatched_tasks.calls == [(PRECHECK_TASK, str(retried.id))]


async def test_retrying_from_awaiting_review_republishes_chunking(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """`awaiting_review` (échec côté vectorisation, par ex.) republie `run_chunking`."""
    fixture = await failed_job(db_session, storage, retry_state=JobState.AWAITING_REVIEW)
    dispatched_tasks.calls.clear()

    retried = await IngestionJobService(db_session).retry(fixture.job)

    assert retried.state is JobState.AWAITING_REVIEW
    assert dispatched_tasks.calls == [(CHUNKING_TASK, str(retried.id))]


async def test_retrying_a_job_that_is_not_failed_answers_conflict(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """Rien à reprendre : le job n'a jamais échoué."""
    fixture = await queued_job(db_session, storage)
    dispatched_tasks.calls.clear()

    with pytest.raises(ConflictError):
        await IngestionJobService(db_session).retry(fixture.job)
    assert dispatched_tasks.calls == []


async def test_retrying_a_non_retryable_cause_answers_conflict(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """Une cause définitive (`retryable=False`) est refusée sans `force=True`."""
    fixture = await failed_job(db_session, storage, retryable=False)
    dispatched_tasks.calls.clear()

    with pytest.raises(ConflictError):
        await IngestionJobService(db_session).retry(fixture.job)
    assert dispatched_tasks.calls == []


async def test_forcing_the_retry_bypasses_the_retryable_guard(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """`force=True` : l'opérateur assume avoir corrigé la cause lui-même."""
    fixture = await failed_job(db_session, storage, retryable=False, retry_state=JobState.QUEUED)
    dispatched_tasks.calls.clear()

    retried = await IngestionJobService(db_session).retry(fixture.job, force=True)

    assert retried.state is JobState.QUEUED
    assert dispatched_tasks.calls == [(PRECHECK_TASK, str(retried.id))]


async def test_retrying_without_a_recorded_cause_answers_conflict(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """Défensif : un `failed` sans `error` (ne devrait plus arriver) reste refusé proprement."""
    fixture = await queued_job(db_session, storage)
    failed = await IngestionJobService(db_session).transition(fixture.job, JobState.FAILED)
    assert failed.error is None
    dispatched_tasks.calls.clear()

    with pytest.raises(ConflictError):
        await IngestionJobService(db_session).retry(failed)
    assert dispatched_tasks.calls == []


async def test_a_retried_job_can_fail_again_independently(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
    dispatched_tasks: DispatchedTasks,
) -> None:
    """La reprise n'est pas un aller simple : un nouvel échec reste possible et journalisé."""
    fixture = await failed_job(db_session, storage, retry_state=JobState.QUEUED)
    service = IngestionJobService(db_session)
    retried = await service.retry(fixture.job)

    second_cause = JobError(
        code="engine_validation",
        message="Toujours cassé.",
        retryable=False,
        retry_state=JobState.QUEUED,
    )
    failed_again = await service.transition(retried, JobState.PRECHECKING)
    failed_again = await service.transition(failed_again, JobState.FAILED, error=second_cause)

    assert failed_again.state is JobState.FAILED
    assert failed_again.error == second_cause.model_dump(mode="json")
    states = await events_of(db_session, fixture.job)
    assert states == [
        JobState.QUEUED,
        JobState.FAILED,
        JobState.QUEUED,
        JobState.PRECHECKING,
        JobState.FAILED,
    ]
