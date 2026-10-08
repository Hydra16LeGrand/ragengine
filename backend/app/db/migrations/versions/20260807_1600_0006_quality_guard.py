"""Garde-fou qualité d'extraction (Phase 11 Étape 4, ADR-024).

Deux colonnes, même geste transactionnel côté application
(`IngestionJobService.transition`, `app/services/jobs.py`) :

- `ingestion_jobs.warning` (JSONB, nullable) : `{code, message, metrics}`,
  même forme et même pattern que `ingestion_jobs.error` (`JobError`,
  ADR-020) — écrit uniquement à la transition finale vers `completed` de
  l'étape d'indexation.
- `documents.has_quality_warning` (boolean, NOT NULL, défaut `false`) :
  projection dénormalisée de « le job courant porte un avertissement » —
  même raisonnement produit que `documents.status` (éviter une jointure pour
  peupler une liste), sans toucher à la machine à états (`DocumentStatus`
  n'est pas modifié).

Aucun backfill : aucune ligne existante n'a de fragments réévalués
rétroactivement — un document déjà `completed` avant cette révision reste
`has_quality_warning = false` jusqu'à sa prochaine ingestion.

Revision ID: 0006_quality_guard
Revises: 0005_document_status_cancelled
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_quality_guard"
down_revision: str | None = "0005_document_status_cancelled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column("warning", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "has_quality_warning",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "has_quality_warning")
    op.drop_column("ingestion_jobs", "warning")
