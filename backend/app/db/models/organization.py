"""Agrégat tenant : `Organization` et sa table d'association `OrganizationMember`.

Premier axe du backlog Premium (ADR-021), le plus structurant : les axes 2
(quotas) et 3 (facturation) porteront sur l'organisation, pas sur `User`
(ADR-022). Cette itération ne pose que le modèle de données et les
permissions ; rien de plus concret pour les axes 2/3 hors périmètre.

`OrganizationMember` est une table N:M dès maintenant : chaque compte reçoit
une organisation personnelle par défaut à sa création (réel **et** invité),
mais peut ensuite être invité dans d'autres organisations sans nouvelle
migration de structure.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import ORGANIZATION_ROLE_ENUM
from app.domain.enums import OrganizationRole

if TYPE_CHECKING:  # pragma: no cover - imports de typage uniquement
    from app.db.models.knowledge_base import KnowledgeBase
    from app.db.models.user import User


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tenant : frontière de propriété des bases de connaissances (ADR-022)."""

    __tablename__ = "organizations"

    name: Mapped[str]
    slug: Mapped[str] = mapped_column(
        unique=True,
        index=True,
        doc="Identifiant lisible, unique **globalement** — à la différence du slug d'une KB.",
    )

    members: Mapped[list[OrganizationMember]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(
        back_populates="organization",
        lazy="raise_on_sql",
        doc=(
            "Sans cascade : supprimer une organisation qui possède encore des "
            "bases est un refus applicatif (`OrganizationService.delete`, `409`), "
            "pas une suppression physique en chaîne — `knowledge_bases.organization_id` "
            "porte `ondelete=RESTRICT`, à la différence de la cascade `User` → "
            "`KnowledgeBase` déjà en place."
        ),
    )


class OrganizationMember(Base, TimestampMixin):
    """Appartenance d'un utilisateur à une organisation, porteuse du rôle tenant.

    Pas de colonne `id` : la paire (organisation, utilisateur) **est**
    l'identité de la ligne, même pattern que `KnowledgeBaseMember` — il
    interdit par construction deux rôles concurrents pour le même couple.
    """

    __tablename__ = "organization_members"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Colonne de tête de la clé primaire : le filtrage par organisation est déjà indexé.",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        doc="Indexé séparément : « mes organisations » n'utilise pas la PK.",
    )
    role: Mapped[OrganizationRole] = mapped_column(
        ORGANIZATION_ROLE_ENUM,
        default=OrganizationRole.MEMBER,
        server_default=text(f"'{OrganizationRole.MEMBER.value}'::organization_role"),
        doc="Le créateur d'une organisation en est `owner` ; toujours au moins un `owner`.",
    )

    organization: Mapped[Organization] = relationship(back_populates="members", lazy="raise_on_sql")
    user: Mapped[User] = relationship(
        back_populates="organization_memberships", lazy="raise_on_sql"
    )
