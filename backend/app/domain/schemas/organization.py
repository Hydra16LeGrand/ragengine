"""Schémas des organisations et de leurs membres (ADR-022).

Contrairement à `KnowledgeBaseRead.principal_role`, `OrganizationRead.principal_role`
n'est jamais `None` pour un accès résolu : il n'existe pas de notion
d'organisation « publique » — un principal qui voit une organisation en est
nécessairement membre (ou administrateur plateforme, résolu à `owner`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.domain.enums import OrganizationRole
from app.domain.schemas.common import InputModel, ReadModel, ResourceName, Slug


class OrganizationCreate(InputModel):
    """Création — `POST /api/v1/organizations`.

    `slug` est facultatif : le service le dérive du nom (`app.domain.slug`)
    quand le client ne l'impose pas. À la différence du slug d'une base de
    connaissances, celui d'une organisation est unique **globalement**, pas
    par propriétaire.
    """

    name: ResourceName
    slug: Slug | None = None


class OrganizationUpdate(InputModel):
    """Mise à jour partielle — `PATCH /api/v1/organizations/{organization_id}` (rôle `owner`)."""

    name: ResourceName | None = None
    slug: Slug | None = None


class OrganizationRead(ReadModel):
    """Organisation telle qu'exposée par l'API."""

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime
    principal_role: OrganizationRole | None = Field(
        default=None,
        description=(
            "Rôle du principal courant sur **cette** organisation. Absent de "
            "l'ORM : jamais peuplé par `model_validate(organization)` seul, "
            "toujours explicitement fourni par le routeur — même logique que "
            "`KnowledgeBaseRead.principal_role`, mais sans jamais valoir `None` "
            "pour un accès résolu (ADR-022 : pas d'organisation publique)."
        ),
    )


class OrganizationMemberUpdate(InputModel):
    """Changement de rôle d'un membre existant — `PUT .../members/{user_id}`."""

    role: OrganizationRole


class OrganizationMemberRead(ReadModel):
    """Appartenance d'un utilisateur à une organisation."""

    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: OrganizationRole
    created_at: datetime
    updated_at: datetime
