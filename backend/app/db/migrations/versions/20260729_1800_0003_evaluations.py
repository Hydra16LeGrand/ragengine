"""Génération d'évaluation (Phase 8) : `evaluation_runs` et `evaluation_items`.

Deux tables neuves, aucun type `ENUM` : `llm_provider_id` est un `TEXT` libre
(catalogue résolu par le registre applicatif, pas par une contrainte de base,
cf. `app.providers.llm.registry`), `retrieval_rank`/`judge_score` sont des
nombres bornés par de simples `CHECK`.

`evaluation_runs.requested_by_id` est nullable et `ON DELETE SET NULL` : ni
`ingestion_jobs` ni `documents` ne tracent l'auteur d'une action dans ce
schéma, il n'y avait donc aucun pattern `*_by` existant à reproduire à
l'identique (écart documenté, ADR-017) — la colonne suit la convention la plus
proche déjà en place pour une référence optionnelle vers `users`.

Revision ID: 0003_evaluations
Revises: 0002_engine_progress_watchdog
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_evaluations"
down_revision: str | None = "0002_engine_progress_watchdog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crée `evaluation_runs` puis `evaluation_items`, dans cet ordre (FK)."""
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(), nullable=True),
        sa.Column("llm_provider_id", sa.Text(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sample_size BETWEEN 1 AND 20",
            name=op.f("ck_evaluation_runs_sample_size_between_1_and_20"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_evaluation_runs_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_evaluation_runs_requested_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
    )
    op.create_index(
        op.f("ix_evaluation_runs_knowledge_base_id"),
        "evaluation_runs",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evaluation_runs_requested_by_id"),
        "evaluation_runs",
        ["requested_by_id"],
        unique=False,
    )

    op.create_table(
        "evaluation_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=False),
        sa.Column("generated_answer", sa.Text(), nullable=False),
        sa.Column("retrieval_rank", sa.Integer(), nullable=True),
        sa.Column("judge_score", sa.Float(), nullable=False),
        sa.Column("judge_rationale", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "judge_score BETWEEN 0.0 AND 1.0",
            name=op.f("ck_evaluation_items_judge_score_between_0_and_1"),
        ),
        sa.CheckConstraint(
            "retrieval_rank IS NULL OR retrieval_rank >= 1",
            name=op.f("ck_evaluation_items_retrieval_rank_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_evaluation_items_evaluation_run_id_evaluation_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documents.id"],
            name=op.f("fk_evaluation_items_source_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_items")),
    )
    op.create_index(
        op.f("ix_evaluation_items_evaluation_run_id"),
        "evaluation_items",
        ["evaluation_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evaluation_items_source_document_id"),
        "evaluation_items",
        ["source_document_id"],
        unique=False,
    )


def downgrade() -> None:
    """Défait le schéma dans l'ordre inverse."""
    op.drop_index(op.f("ix_evaluation_items_source_document_id"), table_name="evaluation_items")
    op.drop_index(op.f("ix_evaluation_items_evaluation_run_id"), table_name="evaluation_items")
    op.drop_table("evaluation_items")
    op.drop_index(op.f("ix_evaluation_runs_requested_by_id"), table_name="evaluation_runs")
    op.drop_index(op.f("ix_evaluation_runs_knowledge_base_id"), table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
