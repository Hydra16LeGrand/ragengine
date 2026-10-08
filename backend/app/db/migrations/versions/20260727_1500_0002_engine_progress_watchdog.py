"""Témoins de vivacité du moteur d'ingestion sur `ingestion_jobs`.

Trois colonnes nullables qui portent la **dernière observation différente**
rapportée par RAGFlow pendant le sondage (`progress`, dernière ligne du journal,
instant de l'observation). Elles sont la condition d'existence de la veille
anti-blocage d'ADR-012 : le délai de garde se mesure depuis cette observation,
pas depuis le début du job, faute de quoi un document volumineux mais sain
serait annulé au même titre qu'un document réellement figé.

Nullables et sans valeur par défaut serveur : un job ouvert avant cette révision,
ou n'ayant jamais atteint la phase de sondage, n'a rien à observer — et le
premier sondage les renseigne toutes les trois d'un coup.

Revision ID: 0002_engine_progress_watchdog
Revises: 0001_initial_schema
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_engine_progress_watchdog"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "ingestion_jobs"
COLUMNS = ("engine_progress", "engine_last_message", "engine_progress_observed_at")


def upgrade() -> None:
    """Ajoute les trois témoins de vivacité du moteur."""
    op.add_column(TABLE, sa.Column("engine_progress", sa.Float(), nullable=True))
    op.add_column(TABLE, sa.Column("engine_last_message", sa.Text(), nullable=True))
    op.add_column(
        TABLE,
        sa.Column("engine_progress_observed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Retire les trois témoins, dans l'ordre inverse de leur ajout."""
    for column in reversed(COLUMNS):
        op.drop_column(TABLE, column)
