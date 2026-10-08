"""Schémas des clés API.

Le jeton en clair n'apparaît que dans `ApiKeyCreated`, réponse unique de la
création. Toutes les autres lectures s'arrêtent au `prefix`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.domain.schemas.common import InputModel, ReadModel, ResourceName
from app.domain.scopes import WILDCARD_SCOPE, available_scopes

DEFAULT_SCOPES: tuple[str, ...] = (WILDCARD_SCOPE,)
"""Portée par défaut — joker complet, pour les clés qui ne restreignent rien."""


class ApiKeyCreate(InputModel):
    """Création d'une clé — `POST /api/v1/api-keys` (réservé aux comptes réels)."""

    name: ResourceName
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def _validate_scopes(cls, value: list[str]) -> list[str]:
        """Refuse toute portée hors catalogue (`app.domain.scopes`) — ou `"*"`.

        Une faute de frappe (`document:write` au lieu de `documents:write`) ne
        doit jamais produire une clé silencieusement privée d'accès : mieux
        vaut un `422` immédiat qu'un `403` incompris des semaines plus tard.
        """
        unknown = sorted(
            scope for scope in value if scope != WILDCARD_SCOPE and scope not in available_scopes()
        )
        if unknown:
            message = f"Portée(s) inconnue(s) : {', '.join(unknown)}."
            raise ValueError(message)
        return value


class ApiKeyUpdate(InputModel):
    """Renommage d'une clé ; la révocation passe par `DELETE`, jamais par un champ."""

    name: ResourceName | None = None


class ApiKeyRead(ReadModel):
    """Clé API telle que listée — sans `key_hash` ni jeton."""

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ApiKeyCreated(ApiKeyRead):
    """Réponse de création : seul instant où le jeton complet est communiqué.

    À construire explicitement, le jeton n'existant pas sur la ligne ORM :
    `ApiKeyCreated(**ApiKeyRead.model_validate(row).model_dump(), token=clear)`.
    """

    token: str = Field(
        description="Jeton `rag_<env>_<43 caractères>` — affiché une seule fois, jamais stocké."
    )
