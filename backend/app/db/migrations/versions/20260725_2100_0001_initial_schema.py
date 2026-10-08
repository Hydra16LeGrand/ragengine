"""Schéma initial : identité, connaissances et ingestion.

Cette révision crée l'intégralité du modèle de `docs/domain-model.md`.

Deux points méritent d'être écrits à la main plutôt qu'autogénérés :

1. **Les types `ENUM`** sont créés et détruits explicitement. Un `DROP TABLE` ne
   supprime pas le type qui l'accompagnait : sans le `DROP TYPE` du
   `downgrade`, un `upgrade` rejoué échouerait sur « type already exists ». Le
   type `job_state` sert d'ailleurs deux tables, il ne peut donc pas être
   attaché à l'une d'elles.
2. **La clé étrangère circulaire** `documents.current_job_id` →
   `ingestion_jobs.id` est posée par un `ALTER` après la création des deux
   tables, aucune des deux ne pouvant précéder l'autre.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_JOB_FK = "fk_documents_current_job_id_ingestion_jobs"

# `create_type=False` : la création et la destruction des types sont pilotées
# explicitement ci-dessous, jamais déduites de la création d'une table.
user_role = postgresql.ENUM("admin", "member", name="user_role", create_type=False)
member_role = postgresql.ENUM("owner", "editor", "viewer", name="member_role", create_type=False)
visibility = postgresql.ENUM("private", "public", name="visibility", create_type=False)
document_status = postgresql.ENUM(
    "pending",
    "processing",
    "awaiting_review",
    "indexed",
    "failed",
    name="document_status",
    create_type=False,
)
job_state = postgresql.ENUM(
    "queued",
    "prechecking",
    "awaiting_review",
    "chunking",
    "embedding",
    "indexing",
    "completed",
    "failed",
    "cancelled",
    name="job_state",
    create_type=False,
)

ENUM_TYPES = (user_role, member_role, visibility, document_status, job_state)


def upgrade() -> None:
    """Crée les types énumérés, les huit tables et leurs contraintes."""
    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.create(bind, checkfirst=False)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", user_role, server_default=sa.text("'member'::user_role"), nullable=False),
        sa.Column("is_guest", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
            "(is_guest AND email IS NULL AND password_hash IS NULL AND expires_at IS NOT NULL)"
            " OR (NOT is_guest AND email IS NOT NULL AND password_hash IS NOT NULL"
            " AND expires_at IS NULL)",
            name=op.f("ck_users_guest_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    # Index fonctionnel : unicité insensible à la casse sans extension `citext`.
    op.create_index(
        "ix_users_email_lower", "users", [sa.literal_column("lower(email)")], unique=True
    )
    op.create_index(op.f("ix_users_expires_at"), "users", ["expires_at"], unique=False)

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_token_hash"), "sessions", ["token_hash"], unique=True)
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{\"*\"}'::text[]"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_api_keys_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
    )
    op.create_index(op.f("ix_api_keys_key_hash"), "api_keys", ["key_hash"], unique=True)
    op.create_index(op.f("ix_api_keys_prefix"), "api_keys", ["prefix"], unique=False)
    op.create_index(op.f("ix_api_keys_user_id"), "api_keys", ["user_id"], unique=False)

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column(
            "visibility",
            visibility,
            server_default=sa.text("'private'::visibility"),
            nullable=False,
        ),
        sa.Column("embedding_model_id", sa.Text(), nullable=False),
        sa.Column("reranker_model_id", sa.Text(), nullable=True),
        sa.Column(
            "provider_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "observability_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "evaluation_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("ragflow_dataset_external_id", sa.Text(), nullable=True),
        sa.Column("document_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            "document_count >= 0",
            name=op.f("ck_knowledge_bases_document_count_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_knowledge_bases_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_bases")),
        sa.UniqueConstraint("owner_id", "slug", name="uq_knowledge_bases_owner_id_slug"),
    )

    op.create_table(
        "knowledge_base_members",
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            member_role,
            server_default=sa.text("'viewer'::member_role"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_knowledge_base_members_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_knowledge_base_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "knowledge_base_id", "user_id", name=op.f("pk_knowledge_base_members")
        ),
    )
    op.create_index(
        op.f("ix_knowledge_base_members_user_id"),
        "knowledge_base_members",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.Text(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column(
            "status",
            document_status,
            server_default=sa.text("'pending'::document_status"),
            nullable=False,
        ),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "detected_languages",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("precheck_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("precheck_decision", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("current_job_id", sa.Uuid(), nullable=True),
        sa.Column("ragflow_document_external_id", sa.Text(), nullable=True),
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
            "page_count IS NULL OR page_count >= 0",
            name=op.f("ck_documents_page_count_non_negative"),
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_documents_size_bytes_non_negative")),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_documents_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(
        op.f("ix_documents_current_job_id"), "documents", ["current_job_id"], unique=False
    )
    op.create_index(
        "ix_documents_knowledge_base_id_checksum_sha256",
        "documents",
        ["knowledge_base_id", "checksum_sha256"],
        unique=False,
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state", job_state, server_default=sa.text("'queued'::job_state"), nullable=False
        ),
        sa.Column(
            "progress_percent", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("celery_task_id", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_ingestion_jobs_attempts_non_negative")),
        sa.CheckConstraint(
            "progress_percent BETWEEN 0 AND 100",
            name=op.f("ck_ingestion_jobs_progress_percent_between_0_and_100"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_ingestion_jobs_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_ingestion_jobs_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_jobs")),
    )
    op.create_index(
        op.f("ix_ingestion_jobs_document_id"), "ingestion_jobs", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_ingestion_jobs_knowledge_base_id"),
        "ingestion_jobs",
        ["knowledge_base_id"],
        unique=False,
    )

    op.create_table(
        "ingestion_job_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("state", job_state, nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["ingestion_jobs.id"],
            name=op.f("fk_ingestion_job_events_job_id_ingestion_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_job_events")),
    )
    op.create_index(
        op.f("ix_ingestion_job_events_job_id"),
        "ingestion_job_events",
        ["job_id"],
        unique=False,
    )

    # Boucle documents ↔ ingestion_jobs : posée une fois les deux tables créées.
    op.create_foreign_key(
        CURRENT_JOB_FK,
        "documents",
        "ingestion_jobs",
        ["current_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Défait le schéma dans l'ordre inverse, types énumérés compris."""
    # La boucle doit être rompue avant de pouvoir supprimer `ingestion_jobs`.
    op.drop_constraint(CURRENT_JOB_FK, "documents", type_="foreignkey")

    op.drop_index(op.f("ix_ingestion_job_events_job_id"), table_name="ingestion_job_events")
    op.drop_table("ingestion_job_events")
    op.drop_index(op.f("ix_ingestion_jobs_knowledge_base_id"), table_name="ingestion_jobs")
    op.drop_index(op.f("ix_ingestion_jobs_document_id"), table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_documents_knowledge_base_id_checksum_sha256", table_name="documents")
    op.drop_index(op.f("ix_documents_current_job_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_knowledge_base_members_user_id"), table_name="knowledge_base_members")
    op.drop_table("knowledge_base_members")
    op.drop_table("knowledge_bases")
    op.drop_index(op.f("ix_api_keys_user_id"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_prefix"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_key_hash"), table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_token_hash"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_users_expires_at"), table_name="users")
    op.drop_index("ix_users_email_lower", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    for enum_type in reversed(ENUM_TYPES):
        enum_type.drop(bind, checkfirst=False)
