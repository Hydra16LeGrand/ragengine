"""Ajoute `cancelled` au type `document_status`.

Défaut trouvé lors des tests manuels de l'interface (2026-08-01) : `JobState.CANCELLED` se
projetait sur `DocumentStatus.PENDING` (`app/domain/state_machine.py`), faute d'une valeur
`cancelled` côté `DocumentStatus` — un document dont le job venait d'être annulé par
l'utilisateur réapparaissait donc « en attente » dans la liste, comme si un traitement restait
à venir. Cette révision ajoute la valeur manquante ; `state_machine.py` et le frontend sont mis
à jour dans le même commit pour projeter réellement `cancelled` désormais.

`ALTER TYPE ... ADD VALUE` est transactionnel depuis PostgreSQL 12 (image `postgres:17-alpine`
du dépôt) tant que la valeur ajoutée n'est pas utilisée dans la même transaction — ce qui est le
cas ici, on ne fait qu'ajouter la valeur.

Revision ID: 0005_document_status_cancelled
Revises: 0004_multi_tenancy
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_document_status_cancelled"
down_revision: str | None = "0004_multi_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_NAME = "document_status"
OLD_VALUES = ("pending", "processing", "awaiting_review", "indexed", "failed")
NEW_VALUE = "cancelled"


def upgrade() -> None:
    """Ajoute la valeur manquante — pas de backfill : aucune ligne existante n'en a besoin
    (les documents déjà réannulés restent `pending`, statu quo assumé pour l'historique)."""
    op.execute(f"ALTER TYPE {ENUM_NAME} ADD VALUE IF NOT EXISTS '{NEW_VALUE}'")


def downgrade() -> None:
    """Retire la valeur — Postgres ne le permet qu'en recréant le type.

    Refuse si une ligne l'utilise encore : un downgrade ne doit jamais corrompre
    silencieusement une donnée en la faisant tomber hors de son propre type.
    """
    bind = op.get_bind()
    in_use = bind.execute(
        sa.text(f"SELECT count(*) FROM documents WHERE status = '{NEW_VALUE}'")
    ).scalar_one()
    if in_use:
        raise RuntimeError(
            f"Downgrade refusé : {in_use} document(s) portent encore le statut "
            f"'{NEW_VALUE}'. Reclasse-les avant de rejouer cette révision en arrière."
        )

    op.execute(f"ALTER TYPE {ENUM_NAME} RENAME TO {ENUM_NAME}_old")
    restored = postgresql.ENUM(*OLD_VALUES, name=ENUM_NAME, create_type=False)
    restored.create(bind, checkfirst=False)
    # Le DEFAULT existant est encore typé sur l'ancien enum : Postgres refuse de
    # changer le type de la colonne tant qu'il n'est pas retiré au préalable
    # ("default for column ... cannot be cast automatically to type ...").
    op.execute("ALTER TABLE documents ALTER COLUMN status DROP DEFAULT")
    op.execute(
        f"ALTER TABLE documents ALTER COLUMN status TYPE {ENUM_NAME} "
        f"USING status::text::{ENUM_NAME}"
    )
    op.execute(f"ALTER TABLE documents ALTER COLUMN status SET DEFAULT 'pending'::{ENUM_NAME}")
    op.execute(f"DROP TYPE {ENUM_NAME}_old")
