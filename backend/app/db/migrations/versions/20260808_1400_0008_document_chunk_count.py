"""Compte de fragments par document (`documents.chunk_count`).

Nouvelle colonne nullable, alimentée par le même geste transactionnel que
`documents.has_quality_warning` (ADR-024,
`app.workers.tasks.ingestion._quality_warning_for_step`,
`IngestionJobService.transition`) : la lecture moteur du garde-fou qualité
porte déjà le total réel de fragments (`EngineDocumentChunksMeta.chunk_count`,
non borné par l'échantillon qualité), il ne coûte rien de le persister au même
moment.

Aucun backfill : comme `has_quality_warning` (`0006_quality_guard`), un
document déjà `completed` avant cette révision reste `chunk_count = NULL`
jusqu'à sa prochaine ingestion — la valeur n'a jamais été calculée pour lui,
contrairement à `has_quality_warning` qui avait un défaut booléen sûr
(`false`) à rétro-projeter.

Revision ID: 0008_document_chunk_count
Revises: 0007_evaluation_run_status
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_document_chunk_count"
down_revision: str | None = "0007_evaluation_run_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("chunk_count", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_documents_chunk_count_non_negative"),
        "documents",
        "chunk_count IS NULL OR chunk_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_documents_chunk_count_non_negative"), "documents", type_="check")
    op.drop_column("documents", "chunk_count")
