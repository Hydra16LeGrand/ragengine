"""Statut des runs d'évaluation + déplafonnement du `CHECK` sur `sample_size`.

Phase 12, Étape 4 (item restant), ADR-028 : un run **asynchrone** (grand
échantillon, `sample_size > MAX_EVALUATION_SAMPLE_SIZE`) est désormais
persisté dès sa création, en `pending`, avant même que `run_async_evaluation`
(Celery) ne produise ses items — voir `app/db/models/evaluation.py` et
`app/services/evaluations.py`. Deux changements, une seule révision :

1. Nouveau type `evaluation_run_status` (`pending`/`completed`/`failed`),
   nouvelle colonne `evaluation_runs.status`, `NOT NULL`, défaut serveur
   `'completed'` — **backfill implicite** : toute ligne existante a été
   produite par l'ancien chemin, entièrement synchrone, donc toujours déjà
   résolue. Aucun `UPDATE` explicite n'est nécessaire, le défaut serveur fait
   le travail à la création de la colonne.
2. Le `CHECK` `sample_size BETWEEN 1 AND 20` est remplacé par
   `sample_size >= 1` : le plafond de 20 restait pertinent pour le seul
   chemin synchrone (`MAX_EVALUATION_SAMPLE_SIZE`, toujours appliqué en
   Python) ; un run asynchrone doit pouvoir dépasser cette valeur, bornée
   séparément par `Settings.evaluation_async_max_sample_size`. Faire porter
   les deux plafonds par la même contrainte `CHECK` aurait empêché de faire
   évoluer l'un sans migration alors que l'autre reste, par construction, un
   réglage d'environnement.

Revision ID: 0007_evaluation_run_status
Revises: 0006_quality_guard
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_evaluation_run_status"
down_revision: str | None = "0006_quality_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_NAME = "evaluation_run_status"
STATUS_VALUES = ("pending", "completed", "failed")

evaluation_run_status = postgresql.ENUM(*STATUS_VALUES, name=ENUM_NAME, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    evaluation_run_status.create(bind, checkfirst=False)
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "status",
            evaluation_run_status,
            server_default=sa.text(f"'completed'::{ENUM_NAME}"),
            nullable=False,
        ),
    )
    op.drop_constraint(
        op.f("ck_evaluation_runs_sample_size_between_1_and_20"),
        "evaluation_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evaluation_runs_sample_size_positive"), "evaluation_runs", "sample_size >= 1"
    )


def downgrade() -> None:
    """Refuse si une ligne dépasse l'ancien plafond ou porte un statut non résolu.

    Même discipline que `0005_document_status_cancelled` : un downgrade ne doit
    jamais faire tomber silencieusement une ligne hors de la contrainte qu'il
    restaure.
    """
    bind = op.get_bind()
    oversized = bind.execute(
        sa.text("SELECT count(*) FROM evaluation_runs WHERE sample_size > 20")
    ).scalar_one()
    if oversized:
        raise RuntimeError(
            f"Downgrade refusé : {oversized} run(s) portent un sample_size > 20. "
            "Reclasse-les avant de rejouer cette révision en arrière."
        )
    unresolved = bind.execute(
        sa.text("SELECT count(*) FROM evaluation_runs WHERE status = 'pending'")
    ).scalar_one()
    if unresolved:
        raise RuntimeError(
            f"Downgrade refusé : {unresolved} run(s) restent 'pending'. "
            "Attends leur résolution (ou marque-les 'failed') avant de rejouer "
            "cette révision en arrière."
        )

    op.drop_constraint(
        op.f("ck_evaluation_runs_sample_size_positive"), "evaluation_runs", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_evaluation_runs_sample_size_between_1_and_20"),
        "evaluation_runs",
        "sample_size BETWEEN 1 AND 20",
    )
    op.drop_column("evaluation_runs", "status")
    op.execute(f"DROP TYPE {ENUM_NAME}")
