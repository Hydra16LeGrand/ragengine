"""Clés API : émission unique du jeton, cloisonnement des comptes, révocation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.security import hash_token
from app.db.models import ApiKey, User
from app.domain.schemas.api_key import ApiKeyCreate
from app.domain.schemas.user import UserCreate
from app.services.identity.api_keys import ApiKeyService
from app.services.identity.errors import AccountRequiredError
from app.services.identity.users import UserService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


async def _account(db: AsyncSession) -> User:
    """Compte réel prêt à détenir des clés."""
    return await UserService(db).register(
        UserCreate(
            email=factories.unique_email("cles"),
            password=SecretStr(PASSWORD),
            display_name="Amara",
        )
    )


async def test_creation_returns_the_token_once_and_stores_only_its_hash(
    db_session: AsyncSession,
) -> None:
    """Le jeton n'existe qu'en réponse ; la base n'en garde que préfixe et SHA-256."""
    user = await _account(db_session)

    issued = await ApiKeyService(db_session).create(user, ApiKeyCreate(name="CI"))

    assert issued.token.startswith("rag_")
    assert issued.api_key.key_hash == hash_token(issued.token)
    assert issued.api_key.prefix == issued.token[:12]
    assert issued.api_key.revoked_at is None
    assert issued.api_key.scopes == ["*"]


async def test_the_plaintext_token_is_nowhere_in_the_table(db_session: AsyncSession) -> None:
    """Impossible de réafficher une clé : c'est ce qui rend la révocation nécessaire."""
    user = await _account(db_session)
    issued = await ApiKeyService(db_session).create(user, ApiKeyCreate(name="CI"))

    rows = (await db_session.execute(select(ApiKey))).scalars().all()

    assert all(issued.token not in f"{row.key_hash}{row.name}" for row in rows)


async def test_a_guest_cannot_create_an_api_key(db_session: AsyncSession) -> None:
    """Matrice §4 : la clé API est réservée aux comptes réels."""
    issued = await factories.ephemeral_session(db_session)

    with pytest.raises(ForbiddenError):
        await ApiKeyService(db_session).create(issued.user, ApiKeyCreate(name="Interdite"))


async def test_the_guest_refusal_points_to_the_elevation_path(db_session: AsyncSession) -> None:
    """Le `type` du Problem Details dit à l'interface quoi proposer."""
    issued = await factories.ephemeral_session(db_session)

    with pytest.raises(AccountRequiredError) as caught:
        await ApiKeyService(db_session).create(issued.user, ApiKeyCreate(name="Interdite"))

    assert caught.value.to_problem().type == "/problems/account-required"
    assert caught.value.to_problem().status == 403


async def test_an_expiry_in_the_past_is_refused(db_session: AsyncSession) -> None:
    """Créer une clé déjà expirée est une erreur d'appel, pas un état valable."""
    user = await _account(db_session)

    with pytest.raises(ValidationError):
        await ApiKeyService(db_session).create(
            user,
            ApiKeyCreate(name="Périmée", expires_at=datetime.now(UTC) - timedelta(minutes=1)),
        )


async def test_listing_only_returns_the_keys_of_the_account(db_session: AsyncSession) -> None:
    """Le cloisonnement est dans la requête, pas dans un filtrage de sortie."""
    service = ApiKeyService(db_session)
    mine = await _account(db_session)
    other = await _account(db_session)
    await service.create(mine, ApiKeyCreate(name="À moi"))
    await service.create(other, ApiKeyCreate(name="À l'autre"))

    rows = await service.list_for(mine)

    assert [row.name for row in rows] == ["À moi"]


async def test_revocation_is_not_destructive_and_is_idempotent(db_session: AsyncSession) -> None:
    """La ligne survit à sa révocation : une clé compromise reste auditable."""
    service = ApiKeyService(db_session)
    user = await _account(db_session)
    issued = await service.create(user, ApiKeyCreate(name="CI"))

    revoked = await service.revoke(user, issued.api_key.id)
    first_revocation = revoked.revoked_at
    again = await service.revoke(user, issued.api_key.id)

    assert first_revocation is not None
    assert again.revoked_at == first_revocation
    assert await db_session.get(ApiKey, issued.api_key.id) is not None


async def test_revoking_someone_elses_key_answers_not_found(db_session: AsyncSession) -> None:
    """`404` et non `403` : l'existence de la clé d'autrui n'est pas confirmée."""
    service = ApiKeyService(db_session)
    mine = await _account(db_session)
    other = await _account(db_session)
    issued = await service.create(other, ApiKeyCreate(name="À l'autre"))

    with pytest.raises(NotFoundError):
        await service.revoke(mine, issued.api_key.id)


async def test_revoking_an_unknown_key_answers_not_found(db_session: AsyncSession) -> None:
    """Un identifiant inventé ne produit pas de 500."""
    user = await _account(db_session)

    with pytest.raises(NotFoundError):
        await ApiKeyService(db_session).revoke(user, uuid.uuid4())
