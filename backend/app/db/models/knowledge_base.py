"""Agrégat connaissances : `KnowledgeBase` et sa table d'association `KnowledgeBaseMember`.

`ProviderConfig` n'est pas une table (ADR-009) : c'est un value object Pydantic
sérialisé dans `provider_config`. Les deux choix structurants — modèle
d'embedding et reranker — sont dupliqués en colonnes pour rester filtrables en
SQL lors d'un changement de modèle.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import MEMBER_ROLE_ENUM, VISIBILITY_ENUM
from app.domain.enums import MemberRole, Visibility

if TYPE_CHECKING:  # pragma: no cover - imports de typage uniquement
    from app.db.models.document import Document
    from app.db.models.ingestion_job import IngestionJob
    from app.db.models.organization import Organization
    from app.db.models.user import User


class KnowledgeBase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Base de connaissances : unité de propriété, de configuration et de recherche."""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        # Le slug est unique **par propriétaire** : deux utilisateurs peuvent
        # nommer leur base « support-technique ». L'index de cette contrainte
        # sert aussi le filtrage par `owner_id`.
        UniqueConstraint("owner_id", "slug", name="uq_knowledge_bases_owner_id_slug"),
        CheckConstraint("document_count >= 0", name="document_count_non_negative"),
    )

    slug: Mapped[str] = mapped_column(
        doc="Identifiant lisible dérivé du nom, unique par propriétaire."
    )
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        index=True,
        doc=(
            "Tenant propriétaire (ADR-022) — dimension orthogonale à `owner_id`, "
            "pas un remplacement. `RESTRICT` : une organisation qui possède "
            "encore des bases ne peut pas être supprimée."
        ),
    )
    visibility: Mapped[Visibility] = mapped_column(
        VISIBILITY_ENUM,
        default=Visibility.PRIVATE,
        server_default=text(f"'{Visibility.PRIVATE.value}'::visibility"),
        doc="`public` ouvre la lecture et le Playground aux invités (ADR-005).",
    )
    chatbot_enabled: Mapped[bool] = mapped_column(
        default=False,
        server_default=text("false"),
        doc=(
            "Publication en chatbot public — indépendante de `visibility` "
            "(ADR-046) : une base peut être publiée en chatbot sans être "
            "listée/lisible en entier, et inversement."
        ),
    )
    chatbot_slug: Mapped[str | None] = mapped_column(
        unique=True,
        default=None,
        doc=(
            "Identifiant public opaque du lien de chatbot, unique globalement "
            "(contrairement à `slug`, unique par propriétaire) — c'est la seule "
            "clé de résolution du lien public, non devinable."
        ),
    )
    chatbot_llm_provider_id: Mapped[str | None] = mapped_column(
        default=None,
        doc=(
            "Fournisseur LLM utilisé pour ce chatbot publié — choisi par le "
            "propriétaire à l'activation, jamais un défaut serveur silencieux "
            "(même principe que `RetrievalChatRequest.llm_provider_id`). "
            "Obligatoire pour activer `chatbot_enabled`."
        ),
    )
    embedding_model_id: Mapped[str] = mapped_column(
        doc="Dénormalisé depuis `provider_config` pour rester interrogeable en SQL."
    )
    reranker_model_id: Mapped[str | None] = mapped_column(default=None)
    provider_config: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("'{}'::jsonb"),
        default=dict,
        doc="Value object `ProviderConfig` sérialisé (ADR-009).",
    )
    observability_enabled: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), doc="Brique optionnelle Langfuse (Phase 8)."
    )
    evaluation_enabled: Mapped[bool] = mapped_column(
        default=False,
        server_default=text("false"),
        doc="Brique optionnelle d'évaluation (Phase 8).",
    )
    ragflow_dataset_external_id: Mapped[str | None] = mapped_column(
        default=None, doc="Clé du système tiers RAGFlow — jamais une clé de jointure interne."
    )
    document_count: Mapped[int] = mapped_column(
        default=0,
        server_default=text("0"),
        doc="Compteur dénormalisé, maintenu par le service d'ingestion.",
    )

    owner: Mapped[User] = relationship(back_populates="owned_knowledge_bases", lazy="raise_on_sql")
    organization: Mapped[Organization] = relationship(
        back_populates="knowledge_bases", lazy="raise_on_sql"
    )
    members: Mapped[list[KnowledgeBaseMember]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    jobs: Mapped[list[IngestionJob]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )


class KnowledgeBaseMember(Base, TimestampMixin):
    """Appartenance d'un utilisateur à une base, porteuse du rôle par base.

    Pas de colonne `id` : la paire (base, utilisateur) **est** l'identité de la
    ligne, ce qui interdit par construction deux rôles concurrents pour le même
    couple.
    """

    __tablename__ = "knowledge_base_members"

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Colonne de tête de la clé primaire : le filtrage par base est déjà indexé.",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        doc="Indexé séparément : « les bases auxquelles j'appartiens » n'utilise pas la PK.",
    )
    role: Mapped[MemberRole] = mapped_column(
        MEMBER_ROLE_ENUM,
        default=MemberRole.VIEWER,
        server_default=text(f"'{MemberRole.VIEWER.value}'::member_role"),
        doc="Le créateur de la base en est `owner` ; une base garde toujours un `owner`.",
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(
        back_populates="members", lazy="raise_on_sql"
    )
    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise_on_sql")
