"""Cas d'usage des jobs d'ingestion : ouverture, lecture, transitions, annulation.

**Ce module est l'écrivain unique de trois choses** (`docs/domain-model.md` §2 et
§3) : `IngestionJob.state`, la projection `Document.status`, et le journal
`IngestionJobEvent`. Les trois sont écrites dans la **même transaction** que la
transition, sans quoi une liste de documents pourrait afficher un état que le job
a déjà quitté, ou une frise de progression manquer une étape.

La légalité du saut n'est pas décidée ici : elle est déléguée à
`app.domain.state_machine`, logique pure partagée avec les futures tâches Celery
(Phase 3) qui appelleront `transition` exactement comme le fait l'API.

Phase 1 : aucun traitement réel n'est déclenché. Un job naît `queued` et y reste
jusqu'à ce qu'un humain l'annule — `celery_task_id` et `progress_percent` ne
seront alimentés qu'en Phase 3/4.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.models import Document, IngestionJob, IngestionJobEvent, KnowledgeBase
from app.domain.enums import JobState, MemberRole
from app.domain.pagination import DEFAULT_PAGE_SIZE, Cursor, apply_cursor_pagination
from app.domain.schemas.ingestion_job import JobError, JobWarning
from app.domain.state_machine import TERMINAL_STATES, assert_transition, document_status_for
from app.services.identity.permissions import KnowledgeBaseAccess, PermissionService
from app.services.identity.principal import Principal
from app.services.knowledge_bases import get_knowledge_base
from app.workers.celery_app import RETRY_TASK_BY_SOURCE_STATE, task_by_name

_logger = logging.getLogger(__name__)

QUEUED_MESSAGE: Final = "Job créé, en attente de traitement."
CANCELLED_MESSAGE: Final = "Job annulé à la demande de l'utilisateur."
RETRY_MESSAGE: Final = "Reprise manuelle du job après échec."

_UNKNOWN_JOB: Final = "Job d'ingestion introuvable."
_ORPHAN_JOB: Final = "Le document de ce job n'existe plus."
_NOT_FAILED_MESSAGE: Final = "Seul un job en échec (`failed`) peut être repris."
_MISSING_CAUSE_MESSAGE: Final = (
    "Ce job en échec ne porte aucune cause structurée exploitable : reprise impossible."
)
_NOT_RETRYABLE_MESSAGE: Final = (
    "La dernière cause d'échec n'est pas marquée reprenable automatiquement. "
    "Passez `force=true` si elle a été corrigée entre-temps."
)


@dataclass(frozen=True, slots=True)
class JobAccess:
    """Job résolu depuis `/jobs/{job_id}` et l'accès à la base qui le porte."""

    job: IngestionJob
    access: KnowledgeBaseAccess

    @property
    def knowledge_base(self) -> KnowledgeBase:
        """Base de connaissances propriétaire du job, déjà chargée."""
        return self.access.knowledge_base


class IngestionJobService:
    """Cas d'usage portant sur `ingestion_jobs` et `ingestion_job_events`."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def open_for(self, document: Document) -> IngestionJob:
        """Ouvre le job initial d'un document et le porte en `current_job_id`.

        Aucune tâche n'est publiée : la file Celery arrive en Phase 3. Le job
        naît donc `queued`, ce que `Document.status = pending` reflète déjà — la
        projection reste cohérente sans traitement réel.
        """
        job = IngestionJob(
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            state=JobState.QUEUED,
        )
        self._db.add(job)
        await self._db.flush([job])

        self._db.add(
            IngestionJobEvent(job_id=job.id, state=JobState.QUEUED, message=QUEUED_MESSAGE)
        )
        document.current_job_id = job.id
        document.status = document_status_for(JobState.QUEUED)
        await self._db.flush()
        await self._db.refresh(job)
        return job

    async def get(self, job_id: uuid.UUID) -> IngestionJob:
        """Charge un job par identifiant, sans arbitrer aucun droit.

        Raises:
            NotFoundError: aucun job sous cet identifiant.
        """
        job = await self._db.get(IngestionJob, job_id)
        if job is None:
            raise NotFoundError(_UNKNOWN_JOB)
        return job

    async def resolve_access(
        self, principal: Principal, job_id: uuid.UUID, *, minimum: MemberRole
    ) -> JobAccess:
        """Résout `/jobs/{job_id}` : le job, puis la matrice §4 sur **sa** base.

        Le chemin ne porte pas `{kb_id}`, donc `require_kb_permission` ne peut
        pas s'y monter : on retrouve la base par la clé dénormalisée du job et on
        délègue la décision à `PermissionService.authorize`, seul point
        d'application. Une base invisible produit un `404`, qui masque du même
        coup l'existence du job.
        """
        job = await self.get(job_id)
        knowledge_base = await get_knowledge_base(self._db, job.knowledge_base_id)
        access = await PermissionService(self._db).authorize(
            principal, knowledge_base, minimum=minimum
        )
        return JobAccess(job=job, access=access)

    async def list_for_knowledge_base(
        self,
        knowledge_base: KnowledgeBase,
        *,
        cursor: str | Cursor | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> Sequence[IngestionJob]:
        """Liste les jobs d'une base — `GET /api/v1/knowledge-bases/{kb_id}/jobs`.

        La colonne dénormalisée `knowledge_base_id` évite la jointure sur
        `documents` (`docs/domain-model.md` §3).
        """
        statement = apply_cursor_pagination(
            select(IngestionJob).where(IngestionJob.knowledge_base_id == knowledge_base.id),
            IngestionJob,
            cursor=cursor,
            limit=limit,
        )
        return (await self._db.execute(statement)).scalars().all()

    async def transition(
        self,
        job: IngestionJob,
        target: JobState,
        *,
        message: str | None = None,
        error: JobError | None = None,
        warning: JobWarning | None = None,
        chunk_count: int | None = None,
    ) -> IngestionJob:
        """Fait passer un job à `target` et projette le tout dans la même transaction.

        Écrit, dans l'ordre : la légalité du saut, l'état du job, ses
        horodatages, `IngestionJob.error`, `IngestionJob.warning` +
        `Document.has_quality_warning`, `Document.chunk_count`, `Document.status`,
        puis le journal d'audit. Aucun autre code n'a le droit d'écrire l'un de
        ces éléments.

        `error` n'a de sens que lorsque `target is JobState.FAILED` — c'est la
        seule façon d'alimenter `IngestionJob.error` (`app.workers.tasks.
        ingestion._fail`, ADR-020). Toute autre cible **efface** la cause
        précédente : reprendre un job en échec (`retry`, ci-dessous) ou le
        faire échouer à nouveau plus tard ne doit jamais laisser une cause
        périmée visible sur `GET /api/v1/jobs/{id}`.

        `warning` suit le même principe pour `target is JobState.COMPLETED`
        (garde-fou qualité, ADR-024, `app.workers.tasks.ingestion.
        _quality_warning_for_step`) : c'est la seule transition qui l'écrit,
        `Document.has_quality_warning` est posé dans le même geste. Aucun
        autre code n'atteint `COMPLETED` sans passer par ce chemin (`assert_
        transition` ne l'autorise que depuis l'étape d'indexation), donc
        aucun risque de valeur périmée à effacer sur les autres transitions.

        `chunk_count` suit le même principe pour `target is JobState.COMPLETED` :
        calculé à la même lecture moteur que `warning`
        (`app.workers.tasks.ingestion._quality_warning_for_step`), toujours
        réécrit (y compris à `None` si ce calcul a échoué) pour ne jamais
        laisser un total périmé d'une ingestion précédente.

        Raises:
            ConflictError: transition illégale (traduite en `409`).
            NotFoundError: le document projeté a disparu.
        """
        assert_transition(job.state, target)

        document = await self._db.get(Document, job.document_id)
        if document is None:  # pragma: no cover - la clé étrangère l'interdit
            raise NotFoundError(_ORPHAN_JOB)

        now = datetime.now(UTC)
        job.state = target
        job.error = error.model_dump(mode="json") if target is JobState.FAILED and error else None
        if target is JobState.COMPLETED:
            job.warning = warning.model_dump(mode="json") if warning else None
            document.has_quality_warning = warning is not None
            document.chunk_count = chunk_count
        if target in TERMINAL_STATES:
            job.finished_at = now
        else:
            # Reprise depuis un état terminal (`failed` → source de l'étape,
            # ADR-020) : le job redevient actif, `finished_at` ne doit plus
            # laisser croire qu'il est arrêté. No-op pour toute transition
            # nominale, qui n'a jamais mis `finished_at` à autre chose que
            # `None` avant d'atteindre un état terminal.
            job.finished_at = None
            if job.started_at is None:
                job.started_at = now
        document.status = document_status_for(target)

        self._db.add(IngestionJobEvent(job_id=job.id, state=target, message=message))
        await self._db.flush()
        await self._db.refresh(job)
        return job

    async def cancel(self, job: IngestionJob) -> IngestionJob:
        """Annule un job — `DELETE /api/v1/jobs/{job_id}`.

        Seuls `queued` et `awaiting_review` mènent à `cancelled`
        (`CANCELLABLE_STATES`) : la garde n'est pas réécrite ici, c'est
        `assert_transition` qui refuse tout autre départ par un `409`. Un job
        déjà terminé, en cours de chunking ou d'indexation n'est donc pas
        annulable — l'interrompre laisserait un index à moitié écrit.

        Le document retombe à `pending` (`document_status_for`), ce qui le rend
        ré-ingérable sans intervention.
        """
        return await self.transition(job, JobState.CANCELLED, message=CANCELLED_MESSAGE)

    async def retry(self, job: IngestionJob, *, force: bool = False) -> IngestionJob:
        """Reprend un job en échec — `POST /api/v1/jobs/{job_id}/retry` (ADR-020).

        Ne repart pas de zéro : le job retransite vers `JobError.retry_state`
        (l'état source de l'étape qui a échoué,
        `app.domain.state_machine.RETRY_SOURCE_STATES`) puis la tâche Celery
        qui porte cette étape est republiée
        (`app.workers.celery_app.RETRY_TASK_BY_SOURCE_STATE`) — même
        correspondance que `_dispatch_successor` utilise déjà pour enchaîner
        le pipeline nominal, rien n'est réinventé.

        Refuse par `409` :

        - un job qui n'est pas `failed` (rien à reprendre) ;
        - un job `failed` sans cause structurée exploitable (ne devrait pas
          arriver : `_fail` en écrit toujours une désormais — défensif) ;
        - un job dont la cause n'est pas marquée `retryable`, sauf `force=True`
          — l'appelant a alors la responsabilité d'avoir vérifié que la cause a
          été corrigée (document, quota, configuration…).

        `attempts` est incrémenté avant la transition : c'est le compteur déjà
        prévu par le modèle (`IngestionJob.attempts`), jusqu'ici jamais écrit.

        Raises:
            ConflictError: job non repris pour l'une des trois raisons ci-dessus.
        """
        if job.state is not JobState.FAILED:
            raise ConflictError(_NOT_FAILED_MESSAGE, extensions={"current_state": job.state.value})

        if not job.error:
            raise ConflictError(_MISSING_CAUSE_MESSAGE)
        error = JobError.model_validate(job.error)

        if not error.retryable and not force:
            raise ConflictError(
                _NOT_RETRYABLE_MESSAGE, extensions={"error_code": error.code, "retryable": False}
            )

        resume_state = error.retry_state
        if resume_state is None or resume_state not in RETRY_TASK_BY_SOURCE_STATE:
            raise ConflictError(_MISSING_CAUSE_MESSAGE)

        job.attempts += 1
        await self.transition(job, resume_state, message=RETRY_MESSAGE)
        await self._publish_after_commit(RETRY_TASK_BY_SOURCE_STATE[resume_state], job.id)
        return job

    async def _publish_after_commit(self, task_name: str, job_id: uuid.UUID) -> None:
        """Commit la transaction courante, **puis** publie la tâche qui va la relire.

        Même règle, et pour la même raison, que `DocumentService.
        _publish_after_commit` (ADR-015) : une tâche publiée avant le `COMMIT`
        est décrochée par un worker qui, sous READ COMMITTED, lirait l'état
        **précédent** — ici un job encore `failed`, ou `attempts` pas encore
        incrémenté. Dupliquée plutôt que partagée avec `DocumentService` : les
        deux services n'ont pas de dépendance mutuelle, et il n'existait pas de
        module tiers déjà responsable de ce couplage transaction/dispatch.
        """
        await self._db.commit()
        task_by_name(task_name).delay(str(job_id))
        _logger.info(
            "Reprise de job publiée", extra={"job_id": str(job_id), "celery_task": task_name}
        )
