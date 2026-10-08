"""Cycle de vie des comptes : inscription et lecture.

L'unicité de l'adresse est arbitrée par **la base** (index unique fonctionnel
`ix_users_email_lower`), jamais par une simple lecture préalable : entre le
`SELECT` et l'`INSERT`, deux requêtes concurrentes peuvent parfaitement viser la
même adresse. La lecture préalable n'est donc là que pour produire un message
clair dans le cas courant ; c'est la violation de contrainte qui fait foi.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.security import hash_password
from app.db.models import User
from app.domain.enums import UserRole
from app.domain.schemas.user import UserCreate
from app.services.organizations import OrganizationService

EMAIL_UNIQUE_INDEX: Final = "ix_users_email_lower"
"""Index unique sur `lower(email)` — la contradiction remonte sous ce nom."""

EMAIL_TAKEN: Final = "Cette adresse e-mail est déjà utilisée."


class UserService:
    """Cas d'usage portant sur la table `users`."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Charge un compte par identifiant."""
        return await self._db.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Charge un compte réel par adresse, en s'appuyant sur l'index fonctionnel."""
        statement = select(User).where(func.lower(User.email) == email.strip().lower())
        return (await self._db.execute(statement)).scalar_one_or_none()

    async def register(self, payload: UserCreate) -> User:
        """Inscrit un compte réel — `POST /api/v1/users`.

        Raises:
            ConflictError: l'adresse est déjà prise.
        """
        email = str(payload.email)
        if await self.get_by_email(email) is not None:
            raise ConflictError(EMAIL_TAKEN)

        user = User(
            email=email,
            password_hash=hash_password(payload.password.get_secret_value()),
            display_name=payload.display_name,
            role=UserRole.MEMBER,
            is_guest=False,
            is_active=True,
            expires_at=None,
        )
        await self.save_unique(user)
        # Sans organisation personnelle, ce compte fraîchement créé ne pourrait
        # créer aucune base (ADR-022 : `organization_id` est requis).
        await OrganizationService(self._db).create_personal(user)
        return user

    async def save_unique(self, user: User) -> None:
        """Écrit la ligne en traduisant la violation d'unicité en `ConflictError`.

        Le `begin_nested()` isole l'écriture dans un point de sauvegarde : après
        l'échec, la session reste utilisable au lieu d'être condamnée au rollback
        complet, ce qui permet au routeur de répondre proprement. L'ajout à la
        session a lieu **dans** le point de sauvegarde, sans quoi l'`INSERT`
        refusé serait rejoué au prochain `flush` (`add` sur une ligne déjà
        persistante est sans effet).
        """
        try:
            async with self._db.begin_nested():
                self._db.add(user)
                await self._db.flush([user])
        except IntegrityError as exc:
            if EMAIL_UNIQUE_INDEX in str(exc.orig):
                raise ConflictError(EMAIL_TAKEN) from exc
            raise
        # `updated_at` est écrit par PostgreSQL (`onupdate=now()`) : après un
        # UPDATE, la colonne est expirée côté ORM. La relire ici évite qu'une
        # sérialisation ultérieure ne déclenche un chargement paresseux dans un
        # contexte synchrone, où l'accès à la base est impossible.
        await self._db.refresh(user)
