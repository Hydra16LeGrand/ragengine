"""Clés LLM tierces fournies par l'utilisateur (`user_llm_keys`).

Une ligne par (utilisateur, fournisseur) — `UserLlmKey`, `app.db.models.
user_llm_key`. `encrypted_key` porte un chiffrement Fernet réversible
(`app.core.security.encrypt_secret`), jamais un hachage : contrairement à
`api_keys.key_hash`, cette valeur doit être ré-envoyée en clair au fournisseur.

Revision ID: 0011_user_llm_keys
Revises: 0010_chatbot_publish
Create Date: 2026-08-30

Note (session du 2026-08-30) : chaînée après 0010, pas 0008 — deux autres
migrations concurrentes (0009_document_theme, 0010_chatbot_publish) ont été
écrites en parallèle sur le même arbre de travail par d'autres forks pendant
cette tâche. Si l'ordre final réel diverge (fusion/rebasage), cette révision
devra être re-chaînée sur la tête effective avant `alembic upgrade`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_user_llm_keys"
down_revision: str | None = "0010_chatbot_publish"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_llm_keys",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "provider IN ('anthropic', 'openai', 'groq')", name="ck_user_llm_keys_provider"
        ),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_llm_keys_user_provider"),
    )
    op.create_index("ix_user_llm_keys_user_id", "user_llm_keys", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_llm_keys_user_id", table_name="user_llm_keys")
    op.drop_table("user_llm_keys")
