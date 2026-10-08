"""Schémas de l'agrégat identité côté compte (`User`).

`UserRead` ne porte **jamais** `password_hash` : la seule façon d'exposer un
secret serait de l'ajouter explicitement ici, et un test l'interdit.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.domain.enums import UserRole
from app.domain.schemas.common import (
    DisplayName,
    InputModel,
    NormalizedEmail,
    Password,
    ReadModel,
)


class UserCreate(InputModel):
    """Inscription publique — `POST /api/v1/users`."""

    email: NormalizedEmail
    password: Password
    display_name: DisplayName


class UserUpdate(InputModel):
    """Administration d'un compte — `PATCH /api/v1/users/{user_id}` (rôle `admin`).

    Champs absents = inchangés : appliquer avec `model_dump(exclude_unset=True)`.
    """

    display_name: DisplayName | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class UserRead(ReadModel):
    """Projection publique d'un compte."""

    id: uuid.UUID
    email: str | None = Field(default=None, description="`null` pour un compte éphémère.")
    display_name: str
    role: UserRole
    is_guest: bool
    is_active: bool
    expires_at: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
