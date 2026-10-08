"""Schémas des bases de connaissances et de leurs membres.

`ProviderConfig` est un **value object** sérialisé en JSONB (ADR-009), pas une
table : il n'a ni identité ni cycle de vie propre. Il reste volontairement
ouvert (`extra="allow"`) tant que la Phase 2 n'a pas figé le catalogue des
options ; le JSONB absorbe donc les nouveaux réglages sans migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.plugins import document_knowledge_base_fields
from app.domain.enums import MemberRole, Visibility
from app.domain.schemas.common import (
    MAX_PROVIDER_ID_LENGTH,
    Description,
    InputModel,
    ReadModel,
    ResourceName,
    Slug,
)


class ProviderConfig(BaseModel):
    """Options fines et seuils des providers d'une base de connaissances.

    Les deux choix structurants (embedding, reranker) sont **aussi** portés par
    des colonnes dédiées pour rester filtrables en SQL ; leur cohérence avec ce
    document a un écrivain unique : le service de mise à jour d'une base.
    """

    model_config = ConfigDict(extra="allow")


class KnowledgeBaseCreate(InputModel):
    """Création — `POST /api/v1/knowledge-bases`.

    `slug` est facultatif : le service le dérive du nom (`app.domain.slug`)
    quand le client ne l'impose pas.
    """

    name: ResourceName
    slug: Slug | None = None
    description: Description | None = None
    organization_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Organisation (tenant) propriétaire. Facultatif : résolue par le "
            "service (ADR-022) — organisation par défaut si le principal n'en "
            "a qu'une, `ValidationError` explicite sinon."
        ),
    )
    visibility: Visibility = Visibility.PRIVATE
    embedding_model_id: str = Field(max_length=MAX_PROVIDER_ID_LENGTH)
    reranker_model_id: str | None = Field(default=None, max_length=MAX_PROVIDER_ID_LENGTH)
    provider_config: ProviderConfig = Field(default_factory=ProviderConfig)
    observability_enabled: bool = False
    evaluation_enabled: bool = False


class KnowledgeBaseUpdate(InputModel):
    """Mise à jour partielle — `PATCH /api/v1/knowledge-bases/{kb_id}` (rôle `owner`).

    Ni `owner_id` ni `document_count` : la propriété et le compteur dénormalisé
    ne se modifient pas depuis l'extérieur.

    Ni `embedding_model_id` : RAGFlow fixe le modèle d'embedding du dataset une
    seule fois, à sa création (`RagflowEngineAdapter.ensure_dataset`, qui
    réutilise la référence existante et n'envoie plus jamais `embedding_model`
    ensuite — vérifié dans l'adaptateur). Le laisser patchable ici ne changerait
    donc jamais ce que RAGFlow embarque réellement : ça ne ferait que mentir
    dans les métadonnées RAGEngine, sans effet sur l'index ni sur la cohérence
    ingestion/retrieval (le vrai risque que ce champ laissait croire pouvoir
    contrôler). `reranker_model_id`, lui, reste patchable : c'est un paramètre
    de recherche transmis à chaque appel, pas une propriété figée du dataset.
    """

    name: ResourceName | None = None
    slug: Slug | None = None
    description: Description | None = None
    visibility: Visibility | None = None
    reranker_model_id: str | None = Field(default=None, max_length=MAX_PROVIDER_ID_LENGTH)
    provider_config: ProviderConfig | None = None
    observability_enabled: bool | None = None
    evaluation_enabled: bool | None = None


class KnowledgeBaseRead(ReadModel):
    """Base de connaissances telle qu'exposée par l'API."""

    # Ouvert aux champs de greffon (`Plugin.knowledge_base_fields`, ADR-069) : lus
    # sur la même ligne `KnowledgeBase`, fusionnés au point unique de
    # sérialisation du routeur (`to_read_model`), et documentés dans le schéma
    # OpenAPI par `document_knowledge_base_fields`. La docstring ci-dessus est
    # la description publiée du schéma : elle ne parle que du contrat.
    model_config = ConfigDict(
        from_attributes=True, extra="allow", json_schema_extra=document_knowledge_base_fields
    )

    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    owner_id: uuid.UUID
    organization_id: uuid.UUID = Field(
        description="Organisation (tenant) propriétaire (ADR-022) — toujours renseignée."
    )
    visibility: Visibility
    embedding_model_id: str
    reranker_model_id: str | None = None
    provider_config: dict[str, Any] = Field(
        default_factory=dict, description="Value object `ProviderConfig` sérialisé."
    )
    observability_enabled: bool
    evaluation_enabled: bool
    ragflow_dataset_external_id: str | None = None
    document_count: int
    created_at: datetime
    updated_at: datetime
    principal_role: MemberRole | None = Field(
        default=None,
        description=(
            "Rôle du principal courant sur **cette** base — `null` si l'accès ne "
            "vient que de sa visibilité `public` (`PermissionService.resolve_access`). "
            "Absent de l'ORM : jamais peuplé par `model_validate(kb)` seul, toujours "
            "explicitement fourni par le routeur."
        ),
    )


class KnowledgeBaseMemberCreate(InputModel):
    """Ajout d'un membre — `PUT /api/v1/knowledge-bases/{kb_id}/members/{user_id}`."""

    user_id: uuid.UUID
    role: MemberRole = MemberRole.VIEWER


class KnowledgeBaseMemberUpdate(InputModel):
    """Changement de rôle d'un membre existant."""

    role: MemberRole


class KnowledgeBaseMemberRead(ReadModel):
    """Appartenance d'un utilisateur à une base."""

    knowledge_base_id: uuid.UUID
    user_id: uuid.UUID
    display_name: str = Field(
        description=(
            "Nom affiché du compte, joint depuis `users`. Absent de la ligne "
            "d'appartenance : jamais peuplé par `model_validate(member)` seul, "
            "toujours fourni explicitement par le routeur depuis la jointure du "
            "service. Volontairement **pas** l'e-mail : l'annuaire est lisible "
            "par l'`owner` de la base, une adresse n'a pas à y circuler."
        )
    )
    role: MemberRole
    created_at: datetime
    updated_at: datetime
