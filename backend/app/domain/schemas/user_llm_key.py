"""Schémas des clés LLM personnelles (Anthropic/OpenAI/Groq).

La clé en clair n'apparaît **jamais** en lecture, y compris juste après sa
création — contrairement à `ApiKeyCreated` (clé API développeur), qui
l'affiche une fois. Une clé LLM personnelle n'a pas besoin d'être montrée à
l'utilisateur après coup : il la connaît déjà, c'est la sienne.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.schemas.common import InputModel, ReadModel
from app.providers.llm.base import LLMVendor


class UserLlmKeyCreate(InputModel):
    """Déclare ou remplace la clé personnelle d'un fournisseur.

    `PUT /api/v1/me/llm-keys/{provider}`.
    """

    api_key: str


class UserLlmKeyRead(ReadModel):
    """Ligne de l'inventaire — jamais la clé elle-même, seulement sa présence."""

    provider: LLMVendor
    created_at: datetime
    updated_at: datetime
