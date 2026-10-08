"""Agrégat identité : `User`, `Session` et `ApiKey`.

Un invité est une **ligne `users` éphémère** et non une entité parallèle
(ADR-009) : l'élévation vers un compte réel est un simple `UPDATE`, et tout le
travail déjà produit reste attaché au même `user_id`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, column, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import USER_ROLE_ENUM
from app.domain.enums import UserRole

if TYPE_CHECKING:  # pragma: no cover - imports de typage uniquement
    from app.db.models.knowledge_base import KnowledgeBase, KnowledgeBaseMember
    from app.db.models.organization import OrganizationMember
    from app.db.models.user_llm_key import UserLlmKey

GUEST_IDENTITY_CONSTRAINT = "ck_users_guest_identity"
"""Nom en base de la contrainte de cohérence invité / compte réel."""

_GUEST_IDENTITY_SQL = (
    "(is_guest AND email IS NULL AND password_hash IS NULL AND expires_at IS NOT NULL)"
    " OR "
    "(NOT is_guest AND email IS NOT NULL AND password_hash IS NOT NULL AND expires_at IS NULL)"
)


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Compte de la plateforme — réel ou invité éphémère."""

    __tablename__ = "users"
    __table_args__ = (
        # La cohérence invité/compte réel est garantie **par la base** : aucune
        # ligne incohérente ne peut entrer, quel que soit le code appelant.
        CheckConstraint(_GUEST_IDENTITY_SQL, name="guest_identity"),
        # Unicité insensible à la casse sans dépendre de l'extension `citext`.
        # Les invités (`email IS NULL`) ne se gênent pas entre eux : deux NULL
        # restent distincts pour un index unique PostgreSQL.
        Index("ix_users_email_lower", func.lower(column("email", Text())), unique=True),
    )

    email: Mapped[str | None] = mapped_column(
        default=None, doc="Adresse normalisée en minuscules ; NULL pour un invité."
    )
    password_hash: Mapped[str | None] = mapped_column(
        default=None, doc="Empreinte argon2id ; NULL pour un invité."
    )
    display_name: Mapped[str] = mapped_column(doc="Nom affiché dans l'interface.")
    role: Mapped[UserRole] = mapped_column(
        USER_ROLE_ENUM,
        default=UserRole.MEMBER,
        server_default=text(f"'{UserRole.MEMBER.value}'::user_role"),
        doc="Rôle plateforme (ADR-009).",
    )
    is_guest: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), doc="Espace éphémère (ADR-005)."
    )
    is_active: Mapped[bool] = mapped_column(
        default=True,
        server_default=text("true"),
        doc="Désactivation logique : un compte n'est jamais supprimé physiquement.",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        default=None,
        index=True,
        doc="Échéance de l'espace invité ; NULL pour un compte réel. Indexé pour la purge.",
    )
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    owned_knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    memberships: Mapped[list[KnowledgeBaseMember]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    organization_memberships: Mapped[list[OrganizationMember]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    llm_keys: Mapped[list[UserLlmKey]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )


class Session(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Session d'interface : jeton opaque stocké haché, révocable immédiatement.

    Contrairement à un JWT auto-porteur, la révocation est effective sans
    attendre l'expiration : la ligne porte `revoked_at`.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(
        unique=True, index=True, doc="SHA-256 du jeton de session ; le clair ne sort qu'au cookie."
    )
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    user_agent: Mapped[str | None] = mapped_column(default=None)
    ip_address: Mapped[str | None] = mapped_column(default=None)

    user: Mapped[User] = relationship(back_populates="sessions", lazy="raise_on_sql")


class ApiKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Clé API rattachée à un compte — le jeton clair n'est affiché qu'à la création."""

    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(doc="Libellé lisible choisi par l'utilisateur.")
    prefix: Mapped[str] = mapped_column(
        index=True, doc="12 premiers caractères du jeton, pour l'affichage et le support."
    )
    key_hash: Mapped[str] = mapped_column(
        unique=True,
        index=True,
        doc="SHA-256 du jeton : haute entropie, aucun KDF lent nécessaire.",
    )
    scopes: Mapped[list[str]] = mapped_column(
        server_default=text("'{\"*\"}'::text[]"),
        doc=(
            "Portées appliquées par `require_scope` (app.api.v1.dependencies) — "
            "catalogue dans app.domain.scopes, `*` = joker rétrocompatible."
        ),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(
        default=None, doc="Révocation non destructive : la ligne reste auditable."
    )

    user: Mapped[User] = relationship(back_populates="api_keys", lazy="raise_on_sql")
