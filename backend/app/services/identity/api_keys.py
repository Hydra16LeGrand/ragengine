"""Clés API : création, liste et révocation.

Le jeton en clair n'est renvoyé qu'une fois, à la création (`docs/domain-model.md`
§1). La base n'en garde que le SHA-256 et un préfixe de 12 caractères : c'est ce
préfixe, et lui seul, qui permet à un utilisateur de reconnaître une clé dans une
liste ou de la désigner au support.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.security import generate_api_key_token, hash_token, token_prefix
from app.db.models import ApiKey, User
from app.domain.pagination import DEFAULT_PAGE_SIZE, Cursor, apply_cursor_pagination
from app.domain.schemas.api_key import ApiKeyCreate
from app.services.identity.errors import AccountRequiredError

_GUEST_FORBIDDEN: Final = (
    "Un espace invité ne peut pas détenir de clé API : convertissez-le en compte réel."
)
_UNKNOWN_KEY: Final = "Clé API inconnue."
_EXPIRY_IN_THE_PAST: Final = "L'échéance d'une clé API doit être postérieure à maintenant."


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    """Clé fraîchement créée et son jeton en clair, communiqué une seule fois."""

    api_key: ApiKey
    token: str


class ApiKeyService:
    """Cas d'usage portant sur la table `api_keys`."""

    def __init__(self, db: AsyncSession, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings if settings is not None else get_settings()

    async def create(self, user: User, payload: ApiKeyCreate) -> IssuedApiKey:
        """Émet une clé rattachée à `user` — `POST /api/v1/api-keys`.

        Raises:
            AccountRequiredError: le compte est un espace invité (matrice §4).
            ValidationError: l'échéance demandée est déjà passée.
        """
        if user.is_guest:
            raise AccountRequiredError(_GUEST_FORBIDDEN)
        if payload.expires_at is not None and payload.expires_at <= datetime.now(UTC):
            raise ValidationError(_EXPIRY_IN_THE_PAST)

        token = generate_api_key_token(self._settings.environment.value)
        api_key = ApiKey(
            user_id=user.id,
            name=payload.name,
            prefix=token_prefix(token),
            key_hash=hash_token(token),
            scopes=list(payload.scopes),
            expires_at=payload.expires_at,
        )
        self._db.add(api_key)
        await self._db.flush([api_key])
        return IssuedApiKey(api_key=api_key, token=token)

    async def list_for(
        self,
        user: User,
        *,
        cursor: str | Cursor | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> Sequence[ApiKey]:
        """Liste les clés d'un compte, révoquées comprises (elles restent auditables).

        Renvoie jusqu'à `limit + 1` lignes : la sentinelle sert à `build_page`
        pour calculer `next_cursor` sans `COUNT`.
        """
        statement = apply_cursor_pagination(
            select(ApiKey).where(ApiKey.user_id == user.id),
            ApiKey,
            cursor=cursor,
            limit=limit,
        )
        return (await self._db.execute(statement)).scalars().all()

    async def revoke(self, user: User, api_key_id: uuid.UUID) -> ApiKey:
        """Révoque une clé — `DELETE /api/v1/api-keys/{api_key_id}`.

        La ligne est conservée : une clé compromise doit rester visible dans le
        journal d'un compte. Idempotent, et silencieux sur les clés d'autrui —
        elles répondent `404`, jamais `403`, pour ne pas confirmer leur existence.

        Raises:
            NotFoundError: clé inexistante ou appartenant à un autre compte.
        """
        statement = select(ApiKey).where(ApiKey.id == api_key_id, ApiKey.user_id == user.id)
        api_key = (await self._db.execute(statement)).scalar_one_or_none()
        if api_key is None:
            raise NotFoundError(_UNKNOWN_KEY)
        if api_key.revoked_at is None:
            api_key.revoked_at = datetime.now(UTC)
            await self._db.flush([api_key])
        return api_key
