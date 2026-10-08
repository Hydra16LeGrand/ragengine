"""Jobs d'ingestion : suivi et annulation.

Aucune route n'écrit d'état arbitraire : il n'existe pas de `PATCH {state}`. Une
transition se demande par l'action qui la porte — ici l'annulation — et sa
légalité est arbitrée par `app.domain.state_machine`, qui répond `409` sur un
saut interdit. C'est ce qui garantit qu'un client ne peut pas déclarer
« indexé » un document qui ne l'est pas.

Comme pour les documents, `GET`/`DELETE /jobs/{job_id}` ne portent pas `{kb_id}`
et ne peuvent donc pas monter `require_kb_permission` : `IngestionJobService`
remonte à la base par la clé dénormalisée du job et délègue la décision à
`PermissionService.authorize`.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.v1.dependencies import CurrentPrincipal, require_kb_permission, require_scope
from app.db.session import DbSession
from app.domain.enums import MemberRole
from app.domain.pagination import Page, PaginationParams, build_page
from app.domain.schemas.ingestion_job import IngestionJobRead
from app.domain.scopes import ApiKeyScope
from app.services.identity.permissions import KnowledgeBaseAccess
from app.services.jobs import IngestionJobService

router = APIRouter(
    tags=["jobs"],
    responses={
        int(HTTPStatus.NOT_FOUND): {
            "description": "Job inexistant, ou base invisible pour le principal."
        }
    },
)

KbRead = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.VIEWER))]
"""Accès en lecture : membre `viewer`, ou lecteur d'une base `public` (ADR-005)."""

JobPath = Annotated[uuid.UUID, Path(description="Identifiant du job d'ingestion.")]

ScopeRead = Depends(require_scope(ApiKeyScope.JOBS_READ))
"""Portée de clé API exigée en lecture — sans effet hors authentification par clé."""

ScopeWrite = Depends(require_scope(ApiKeyScope.JOBS_WRITE))
"""Portée de clé API exigée en écriture (annulation)."""


@router.get(
    "/knowledge-bases/{kb_id}/jobs",
    summary="Lister les jobs d'ingestion d'une base",
    description=(
        "Liste paginée par curseur, du plus récent au plus ancien. C'est la "
        "source de la frise de progression de l'interface."
    ),
    dependencies=[ScopeRead],
)
async def list_jobs(
    access: KbRead,
    db: DbSession,
    pagination: Annotated[PaginationParams, Query()],
) -> Page[IngestionJobRead]:
    """Liste les jobs de la base résolue par la dépendance d'autorisation."""
    rows = await IngestionJobService(db).list_for_knowledge_base(
        access.knowledge_base, cursor=pagination.decoded_cursor(), limit=pagination.limit
    )
    return build_page(rows, serializer=IngestionJobRead.model_validate, limit=pagination.limit)


@router.get(
    "/jobs/{job_id}",
    summary="Suivre un job d'ingestion",
    description=(
        "État, avancement et cause d'échec éventuelle. `celery_task_id` n'est pas "
        "exposé : c'est un détail d'infrastructure susceptible de changer de "
        "moteur de file."
    ),
    dependencies=[ScopeRead],
)
async def read_job(job_id: JobPath, principal: CurrentPrincipal, db: DbSession) -> IngestionJobRead:
    """Renvoie un job, si la base qui le porte est lisible par le principal."""
    resolved = await IngestionJobService(db).resolve_access(
        principal, job_id, minimum=MemberRole.VIEWER
    )
    return IngestionJobRead.model_validate(resolved.job)


@router.delete(
    "/jobs/{job_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Annuler un job d'ingestion",
    description=(
        "Fait passer le job à `cancelled` et ramène son document à `pending`, "
        "donc ré-ingérable. Seuls les états `queued` et `awaiting_review` sont "
        "annulables : sur tout autre état la réponse est `409`, avec les états "
        "atteignables en extension du Problem Details."
    ),
    responses={
        int(HTTPStatus.CONFLICT): {"description": "Job non annulable depuis son état courant."}
    },
    dependencies=[ScopeWrite],
)
async def cancel_job(job_id: JobPath, principal: CurrentPrincipal, db: DbSession) -> None:
    """Annule un job d'ingestion."""
    service = IngestionJobService(db)
    resolved = await service.resolve_access(principal, job_id, minimum=MemberRole.EDITOR)
    await service.cancel(resolved.job)


@router.post(
    "/jobs/{job_id}/retry",
    status_code=HTTPStatus.ACCEPTED,
    summary="Reprendre un job d'ingestion en échec",
    description=(
        "Relance le pipeline à partir de l'étape qui a échoué, sans repartir de "
        "zéro : le job retransite vers l'état source de cette étape puis la "
        "tâche Celery correspondante est republiée. Refusé (`409`) si le job "
        "n'est pas `failed`, ou si la dernière cause d'échec (`error.retryable`) "
        "n'est pas marquée reprenable automatiquement — passez `force=true` si "
        "elle a été corrigée entre-temps."
    ),
    responses={
        int(HTTPStatus.CONFLICT): {
            "description": "Job non `failed`, ou échec non reprenable sans `force=true`."
        }
    },
    dependencies=[ScopeWrite],
)
async def retry_job(
    job_id: JobPath,
    principal: CurrentPrincipal,
    db: DbSession,
    force: Annotated[
        bool, Query(description="Reprend même si `error.retryable` est faux.")
    ] = False,
) -> IngestionJobRead:
    """Reprend un job d'ingestion en échec."""
    service = IngestionJobService(db)
    resolved = await service.resolve_access(principal, job_id, minimum=MemberRole.EDITOR)
    job = await service.retry(resolved.job, force=force)
    return IngestionJobRead.model_validate(job)
