"""Clés LLM personnelles : déclaration, liste, révocation, résolution.

Réservé aux comptes réels — même règle que les clés API développeur
(`ApiKeyService`) : un espace invité est éphémère, une clé de fournisseur
tierce n'a pas vocation à lui survivre ni à circuler avec lui.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.core.security import decrypt_secret, encrypt_secret
from app.db.models import User, UserLlmKey
from app.providers.llm.base import LLMVendor
from app.services.identity.errors import AccountRequiredError

_GUEST_FORBIDDEN = (
    "Un espace invité ne peut pas détenir de clé LLM personnelle : convertissez-le en compte réel."
)
_UNKNOWN_KEY = "Aucune clé personnelle n'est déclarée pour ce fournisseur."


class UserLlmKeyService:
    """Cas d'usage portant sur `user_llm_keys`."""

    def __init__(self, db: AsyncSession, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings if settings is not None else get_settings()

    async def list_keys(self, user: User) -> Sequence[UserLlmKey]:
        statement = (
            select(UserLlmKey).where(UserLlmKey.user_id == user.id).order_by(UserLlmKey.provider)
        )
        result = await self._db.scalars(statement)
        return result.all()

    async def set_key(self, user: User, provider: LLMVendor, api_key: str) -> UserLlmKey:
        """Déclare ou remplace la clé d'un fournisseur (une ligne par couple).

        Raises:
            AccountRequiredError: le compte est un espace invité.
        """
        if user.is_guest:
            raise AccountRequiredError(_GUEST_FORBIDDEN)

        existing = await self._get(user, provider)
        encrypted = encrypt_secret(api_key, self._settings.secret_key.get_secret_value())
        if existing is not None:
            existing.encrypted_key = encrypted
            await self._db.flush()
            return existing

        row = UserLlmKey(user_id=user.id, provider=provider, encrypted_key=encrypted)
        self._db.add(row)
        await self._db.flush()
        return row

    async def remove_key(self, user: User, provider: LLMVendor) -> None:
        """Supprime la clé personnelle d'un fournisseur.

        Raises:
            NotFoundError: aucune clé déclarée pour ce fournisseur.
        """
        row = await self._get(user, provider)
        if row is None:
            raise NotFoundError(_UNKNOWN_KEY)
        await self._db.delete(row)
        await self._db.flush()

    async def resolve_key(self, user: User, provider: LLMVendor) -> str | None:
        """Clé déchiffrée de l'utilisateur pour ce fournisseur, `None` si absente.

        Seul point du service qui déchiffre — `RetrievalService.chat` l'appelle
        juste avant l'appel sortant, jamais pour de l'affichage (`UserLlmKeyRead`
        ne porte jamais la clé, cf. sa docstring).
        """
        row = await self._get(user, provider)
        if row is None:
            return None
        return decrypt_secret(row.encrypted_key, self._settings.secret_key.get_secret_value())

    async def _get(self, user: User, provider: LLMVendor) -> UserLlmKey | None:
        statement = select(UserLlmKey).where(
            UserLlmKey.user_id == user.id, UserLlmKey.provider == provider
        )
        row: UserLlmKey | None = await self._db.scalar(statement)
        return row
