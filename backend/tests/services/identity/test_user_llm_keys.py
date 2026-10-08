"""Clés LLM personnelles : déclaration, remplacement, révocation, cloisonnement invité."""

from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.db.models import User
from app.domain.schemas.user import UserCreate
from app.services.identity.errors import AccountRequiredError
from app.services.identity.user_llm_keys import UserLlmKeyService
from app.services.identity.users import UserService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"
_SETTINGS = Settings(RAGENGINE_SECRET_KEY="un-secret-de-test-suffisamment-long")


async def _account(db: AsyncSession) -> User:
    return await UserService(db).register(
        UserCreate(
            email=factories.unique_email("llmkey"),
            password=SecretStr(PASSWORD),
            display_name="Amara",
        )
    )


async def test_set_key_then_resolve_key_round_trips_in_clear(db_session: AsyncSession) -> None:
    """La clé ressort exactement comme fournie — le chiffrement est réversible."""
    user = await _account(db_session)
    service = UserLlmKeyService(db_session, _SETTINGS)

    await service.set_key(user, "anthropic", "sk-ant-perso-123")

    assert await service.resolve_key(user, "anthropic") == "sk-ant-perso-123"


async def test_resolve_key_is_none_when_nothing_declared(db_session: AsyncSession) -> None:
    """Chemin par défaut : aucune clé personnelle → `None`, pas une erreur."""
    user = await _account(db_session)

    resolved = await UserLlmKeyService(db_session, _SETTINGS).resolve_key(user, "openai")

    assert resolved is None


async def test_set_key_twice_replaces_rather_than_duplicates(db_session: AsyncSession) -> None:
    """Une seconde déclaration pour le même fournisseur remplace, n'empile pas."""
    user = await _account(db_session)
    service = UserLlmKeyService(db_session, _SETTINGS)

    await service.set_key(user, "groq", "gsk-ancienne")
    await service.set_key(user, "groq", "gsk-nouvelle")

    keys = await service.list_keys(user)
    assert len(keys) == 1
    assert await service.resolve_key(user, "groq") == "gsk-nouvelle"


async def test_remove_key_then_resolve_returns_none(db_session: AsyncSession) -> None:
    user = await _account(db_session)
    service = UserLlmKeyService(db_session, _SETTINGS)
    await service.set_key(user, "anthropic", "sk-ant-a-retirer")

    await service.remove_key(user, "anthropic")

    assert await service.resolve_key(user, "anthropic") is None


async def test_remove_key_without_one_declared_raises_not_found(db_session: AsyncSession) -> None:
    user = await _account(db_session)

    try:
        await UserLlmKeyService(db_session, _SETTINGS).remove_key(user, "openai")
        raised = False
    except NotFoundError:
        raised = True
    assert raised


async def test_a_guest_cannot_declare_a_personal_llm_key(db_session: AsyncSession) -> None:
    """Même cloisonnement que les clés API développeur (`ApiKeyService`)."""
    issued = await factories.ephemeral_session(db_session)

    caught: AccountRequiredError | None = None
    try:
        await UserLlmKeyService(db_session, _SETTINGS).set_key(
            issued.user, "anthropic", "sk-ant-invite"
        )
    except AccountRequiredError as error:
        caught = error
    assert caught is not None
    assert caught.to_problem().status == 403
