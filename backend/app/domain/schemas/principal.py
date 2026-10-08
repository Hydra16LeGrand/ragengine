"""Schémas du **principal courant** — réponse de `GET /api/v1/me`.

`AuthMethod` vit ici et non dans `app.domain.enums` : ce dernier est la source
de vérité des types `ENUM` **PostgreSQL**, or la méthode d'authentification
n'est jamais persistée. C'est une valeur de contrat d'API, pas une colonne.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field

from app.core.plugins import document_principal_fields
from app.domain.enums import UserRole
from app.domain.schemas.common import ReadModel
from app.domain.schemas.user import UserRead


class AuthMethod(StrEnum):
    """Moyen par lequel le principal a été résolu (`docs/domain-model.md` §6)."""

    API_KEY = "api_key"
    SESSION = "session"
    ANONYMOUS = "anonymous"


class PrincipalRead(ReadModel):
    """Principal courant : qui appelle et comment."""

    # Ouvert aux champs de greffon (`Plugin.principal_fields`, ADR-069) : ils sont
    # fusionnés par `app.core.plugins.principal_extra_fields` et documentés dans
    # le schéma OpenAPI par `document_principal_fields`.
    model_config = ConfigDict(
        from_attributes=True, extra="allow", json_schema_extra=document_principal_fields
    )

    auth_method: AuthMethod
    user: UserRead | None = Field(default=None, description="`null` pour un appel anonyme.")
    role: UserRole | None = Field(default=None, description="Rôle plateforme ; `null` si anonyme.")
    is_guest: bool = Field(default=False, description="`true` pour un espace éphémère.")
