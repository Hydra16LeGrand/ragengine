"""Schémas des jobs d'ingestion et de leur journal d'événements.

Aucun schéma d'écriture de l'état : une transition ne se demande pas par un
`PATCH state`, elle passe par le service dédié qui valide sa légalité
(`app.domain.state_machine`). L'annulation est un `DELETE /jobs/{job_id}`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import JobState
from app.domain.schemas.common import InputModel, ReadModel


class JobError(BaseModel):
    """Cause du dernier échec, sérialisée dans `ingestion_jobs.error`.

    Écrite exclusivement par `app.workers.tasks.ingestion._fail`, au moment où
    un job bascule vers `failed` (`IngestionJobService.transition`, ADR-020).
    """

    code: str = Field(description="Code stable, exploitable par l'interface.")
    message: str
    retryable: bool = Field(description="Une nouvelle tentative a-t-elle une chance d'aboutir ?")
    retry_state: JobState | None = Field(
        default=None,
        description=(
            "État vers lequel `POST /api/v1/jobs/{id}/retry` ferait retransiter le job — "
            "l'état *source* de l'étape qui a échoué (`app.workers.tasks.ingestion.Step.source`), "
            "jamais l'état qu'elle écrit elle-même. `None` seulement si l'échec n'a pas pu être "
            "rattaché à une étape connue (ne devrait pas arriver en pratique)."
        ),
    )


class JobWarning(BaseModel):
    """Garde-fou qualité d'extraction (ADR-024) : signal non bloquant sur un job réussi.

    Distinct de `JobError` : le job a bien atteint `completed` (le moteur n'a
    signalé aucun échec), mais le contenu produit est mesurablement suspect —
    0 fragment, une majorité de fragments dégénérés, ou une perte sévère de
    texte source (PDF). Écrit **uniquement** par
    `app.workers.tasks.ingestion._quality_warning_for_step`, à la transition
    finale vers `JobState.COMPLETED` de l'étape d'indexation — jamais à une
    transition intermédiaire, jamais sur un échec.
    """

    code: str = Field(
        description=(
            "Code stable, exploitable par l'interface : `zero_chunks`, "
            "`degenerate_content` ou `low_source_coverage` (ADR-024 §3)."
        )
    )
    message: str = Field(description="Constat lisible, avec le chiffre qui le fonde.")
    metrics: dict[str, float | int | None] = Field(
        description=(
            "Valeurs brutes ayant motivé le déclenchement — jamais de métrique "
            "`None` traitée comme un échec."
        )
    )


class IngestionJobCreate(InputModel):
    """Ouverture d'un job — usage interne du service d'ingestion."""

    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID


class IngestionJobEventRead(ReadModel):
    """Entrée du journal des transitions (frise de progression, piste d'audit)."""

    id: uuid.UUID
    job_id: uuid.UUID
    state: JobState
    message: str | None = None
    created_at: datetime


class IngestionJobRead(ReadModel):
    """Job d'ingestion tel qu'exposé par `GET /api/v1/jobs/{job_id}`.

    `celery_task_id` n'est pas exposé : c'est un détail d'infrastructure, sans
    valeur pour le client et susceptible de changer de moteur de file.
    """

    id: uuid.UUID
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    state: JobState
    progress_percent: int
    attempts: int
    error: JobError | None = None
    warning: JobWarning | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
