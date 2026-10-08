"""Inscription d'un compte réel."""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.security import verify_password
from app.domain.enums import UserRole
from app.domain.schemas.user import UserCreate
from app.services.identity.users import UserService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


def _registration(email: str | None = None) -> UserCreate:
    """Charge utile d'inscription valide."""
    return UserCreate(
        email=email or factories.unique_email("inscrit"),
        password=SecretStr(PASSWORD),
        display_name="Amara",
    )


async def test_registration_creates_a_real_account(db_session: AsyncSession) -> None:
    """Le compte créé satisfait la contrainte d'identité d'un compte réel."""
    payload = _registration()

    user = await UserService(db_session).register(payload)

    assert user.id is not None
    assert user.email == str(payload.email)
    assert user.is_guest is False
    assert user.expires_at is None
    assert user.role is UserRole.MEMBER


async def test_registration_stores_an_argon2_hash_not_the_password(
    db_session: AsyncSession,
) -> None:
    """Le mot de passe n'est jamais stocké en clair, ni réduit à un condensat rapide."""
    user = await UserService(db_session).register(_registration())

    assert user.password_hash is not None
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(PASSWORD, user.password_hash) is True


async def test_a_taken_email_is_refused(db_session: AsyncSession) -> None:
    """Deux comptes ne peuvent pas partager une adresse : `409`."""
    service = UserService(db_session)
    email = factories.unique_email("doublon")
    await service.register(_registration(email))

    with pytest.raises(ConflictError):
        await service.register(_registration(email))


async def test_a_taken_email_is_refused_whatever_the_case(db_session: AsyncSession) -> None:
    """L'unicité porte sur `lower(email)` : la casse ne crée pas un second compte."""
    service = UserService(db_session)
    email = factories.unique_email("casse")
    await service.register(_registration(email))

    with pytest.raises(ConflictError):
        await service.register(_registration(email.upper()))
