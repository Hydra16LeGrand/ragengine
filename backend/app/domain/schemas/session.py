"""Schémas des sessions d'interface.

`SessionRead` décrit une session **sans** son jeton : `token_hash` ne sort pas
de la base, et le jeton en clair ne circule que dans le cookie `HttpOnly`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import SecretStr

from app.domain.schemas.common import InputModel, NormalizedEmail, ReadModel


class SessionCreate(InputModel):
    """Connexion — `POST /api/v1/sessions`."""

    email: NormalizedEmail
    # Pas de contrainte de longueur ici : refuser un mot de passe trop court à
    # la connexion révélerait la politique appliquée aux comptes existants.
    password: SecretStr


class SessionRead(ReadModel):
    """Session active, telle qu'affichée dans les réglages de sécurité."""

    id: uuid.UUID
    user_id: uuid.UUID
    expires_at: datetime
    revoked_at: datetime | None = None
    user_agent: str | None = None
    ip_address: str | None = None
    created_at: datetime
