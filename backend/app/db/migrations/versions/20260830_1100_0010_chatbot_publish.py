"""Publication d'une base en chatbot public (`knowledge_bases.chatbot_enabled`/`chatbot_slug`).

Décision utilisateur du 2026-08-30 : « je veux que le déploiement en chatbot
puisse être dans la v1 sinon on a pas de produit ». Mécanisme indépendant de
`visibility` (ADR-046) — une base peut être publiée en chatbot sans être
listée/lisible en entier par un anonyme, et inversement.

`chatbot_slug` est unique **globalement** (contrairement à `slug`, unique par
propriétaire) : c'est un jeton d'URL publique, sa collision inter-propriétaires
n'a pas de sens.

Revision ID: 0010_chatbot_publish
Revises: 0009_document_theme
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_chatbot_publish"
down_revision: str | None = "0009_document_theme"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column("chatbot_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # `sa.Text()`, pas `sa.String()` : `Base.type_annotation_map` (`app/db/base.py`)
    # projette `str` sur `Text`, jamais `VARCHAR` — un `String()` ici dériverait
    # silencieusement du modèle (`test_migrated_schema_matches_the_models`).
    op.add_column("knowledge_bases", sa.Column("chatbot_slug", sa.Text(), nullable=True))
    op.add_column("knowledge_bases", sa.Column("chatbot_llm_provider_id", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_knowledge_bases_chatbot_slug", "knowledge_bases", ["chatbot_slug"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_knowledge_bases_chatbot_slug", "knowledge_bases", type_="unique")
    op.drop_column("knowledge_bases", "chatbot_llm_provider_id")
    op.drop_column("knowledge_bases", "chatbot_slug")
    op.drop_column("knowledge_bases", "chatbot_enabled")
