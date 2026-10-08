"""Thème de document, saisi par l'utilisateur (`documents.theme`).

Organisation par sujet à l'intérieur d'une base de connaissances (décision
utilisateur du 2026-08-30 : une thématisation par métadonnée filtrable plutôt
qu'une hiérarchie de bases imbriquées — même patron que `detected_languages`/
`content_type`, filtrables au retrieval depuis ADR-045, mais celui-ci est saisi
par l'utilisateur, jamais déduit).

Colonne nullable, aucun backfill : un document déjà ingéré n'a simplement pas
de thème tant qu'on ne le lui attribue pas via `PATCH /documents/{id}`.

Revision ID: 0009_document_theme
Revises: 0008_document_chunk_count
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_document_theme"
down_revision: str | None = "0008_document_chunk_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `sa.Text()`, pas `sa.String()` : `Base.type_annotation_map` (`app/db/base.py`)
    # projette `str` sur `Text`, jamais `VARCHAR` — un `String()` ici dériverait
    # du modèle et ferait échouer `test_migrated_schema_matches_the_models`.
    op.add_column("documents", sa.Column("theme", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "theme")
