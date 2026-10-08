"""Le principal courant et sa résolution (`docs/domain-model.md` §6).

**Ordre de résolution, et pourquoi il n'y a pas de repli silencieux :**

1. En-tête `Authorization` → clé API. S'il est présent, c'est **le seul**
   identifiant considéré : un client machine qui présente une clé révoquée doit
   l'apprendre par un `401`, pas être silencieusement dégradé en anonyme puis se
   voir refuser l'accès pour une raison différente. Le cookie n'est même pas lu.
2. Sinon, cookie de session. Un cookie inconnu, expiré, révoqué ou rattaché à un
   compte désactivé **ne produit pas** de `401` : l'expiration d'un cookie est un
   état normal d'un navigateur, et elle ne doit pas empêcher de lire les bases
   publiques. Le principal redevient anonyme, et le `401`
   (`/problems/authentication-required`) ne survient qu'à la première action
   persistante — c'est exactement ce qui déclenche le parcours d'élévation.
3. Ni l'un ni l'autre → principal anonyme.

L'asymétrie entre 1 et 2 est délibérée : l'en-tête `Authorization` est posé
explicitement par un développeur à chaque requête, le cookie est rejoué
automatiquement par un navigateur bien après que l'utilisateur l'ait oublié.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_token
from app.db.models import ApiKey, User
from app.db.models import Session as UserSession
from app.domain.enums import UserRole
from app.domain.schemas.principal import AuthMethod
from app.services.identity.errors import (
    AccountRequiredError,
    AuthenticationRequiredError,
    InvalidCredentialsError,
)

BEARER_SCHEME: Final = "bearer"
"""Seul schéma d'authentification HTTP accepté (CONTRIBUTING.md §4)."""

_UNSUPPORTED_SCHEME: Final = "Schéma d'authentification non supporté : utiliser `Bearer <clé API>`."
_INVALID_API_KEY: Final = "Clé API inconnue, révoquée ou expirée."


@dataclass(frozen=True, slots=True)
class Principal:
    """Qui appelle l'API, et par quel moyen il a été authentifié.

    Immuable : une dépendance ne peut pas muter le principal d'une requête pour
    en élargir les droits.
    """

    auth_method: AuthMethod
    user: User | None = None
    api_key: ApiKey | None = None
    session: UserSession | None = None

    @property
    def is_authenticated(self) -> bool:
        """`True` dès qu'un compte — réel ou invité — a été résolu."""
        return self.user is not None

    @property
    def is_guest(self) -> bool:
        """`True` pour un espace éphémère (ADR-009)."""
        return self.user is not None and self.user.is_guest

    @property
    def is_admin(self) -> bool:
        """`True` pour un administrateur de la plateforme."""
        return self.role is UserRole.ADMIN

    @property
    def role(self) -> UserRole | None:
        """Rôle plateforme, ou `None` si le principal est anonyme."""
        return self.user.role if self.user is not None else None

    @property
    def user_id(self) -> uuid.UUID | None:
        """Identifiant du compte, ou `None` si anonyme."""
        return self.user.id if self.user is not None else None

    def require_user(self) -> User:
        """Renvoie le compte courant.

        Raises:
            AuthenticationRequiredError: le principal est anonyme.
        """
        if self.user is None:
            raise AuthenticationRequiredError(
                "Cette action requiert un compte connecté ou un espace invité."
            )
        return self.user

    def require_account(self) -> User:
        """Renvoie le compte courant en exigeant qu'il ne soit **pas** un invité.

        Raises:
            AuthenticationRequiredError: le principal est anonyme.
            AccountRequiredError: le principal est un espace invité.
        """
        user = self.require_user()
        if user.is_guest:
            raise AccountRequiredError(
                "Cette action requiert un compte réel : convertissez votre espace invité."
            )
        return user


ANONYMOUS: Final = Principal(auth_method=AuthMethod.ANONYMOUS)
"""Principal des requêtes sans identifiant — lecture des bases publiques uniquement."""


def parse_bearer_token(authorization: str | None) -> str | None:
    """Extrait le jeton d'un en-tête `Authorization: Bearer <jeton>`.

    Renvoie `None` si l'en-tête est absent ou vide.

    Raises:
        InvalidCredentialsError: en-tête présent mais mal formé, ou schéma autre
            que `Bearer` — un identifiant illisible est une erreur du client, pas
            une requête anonyme.
    """
    if authorization is None or not authorization.strip():
        return None
    scheme, _, credentials = authorization.strip().partition(" ")
    if scheme.lower() != BEARER_SCHEME or not credentials.strip():
        raise InvalidCredentialsError(_UNSUPPORTED_SCHEME)
    return credentials.strip()


def _is_usable(user: User, *, now: datetime) -> bool:
    """Un compte désactivé, ou un invité déjà expiré, n'authentifie plus rien."""
    if not user.is_active:
        return False
    return not (user.expires_at is not None and user.expires_at <= now)


class PrincipalResolver:
    """Résout le principal d'une requête à partir de ses identifiants transportés."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def resolve(
        self, *, authorization: str | None = None, session_token: str | None = None
    ) -> Principal:
        """Applique l'ordre de résolution documenté en tête de module."""
        api_key_token = parse_bearer_token(authorization)
        if api_key_token is not None:
            return await self.resolve_api_key(api_key_token)
        if session_token:
            resolved = await self.resolve_session(session_token)
            if resolved is not None:
                return resolved
        return ANONYMOUS

    async def resolve_api_key(self, token: str) -> Principal:
        """Résout une clé API et met à jour son `last_used_at`.

        Raises:
            InvalidCredentialsError: clé inconnue, révoquée, expirée, ou compte
                inutilisable.
        """
        now = datetime.now(UTC)
        statement = (
            select(ApiKey)
            # `lazy="raise_on_sql"` sur la relation : le compte doit être chargé
            # explicitement, sinon la lecture de `api_key.user` lèverait.
            .options(selectinload(ApiKey.user))
            .where(ApiKey.key_hash == hash_token(token))
        )
        api_key = (await self._db.execute(statement)).scalar_one_or_none()
        if api_key is None or api_key.revoked_at is not None:
            raise InvalidCredentialsError(_INVALID_API_KEY)
        if api_key.expires_at is not None and api_key.expires_at <= now:
            raise InvalidCredentialsError(_INVALID_API_KEY)
        if not _is_usable(api_key.user, now=now):
            raise InvalidCredentialsError(_INVALID_API_KEY)

        api_key.last_used_at = now
        return Principal(auth_method=AuthMethod.API_KEY, user=api_key.user, api_key=api_key)

    async def resolve_session(self, token: str) -> Principal | None:
        """Résout un jeton de session, ou `None` s'il n'est plus valable.

        Le `None` est volontaire : voir l'asymétrie documentée en tête de module.
        """
        now = datetime.now(UTC)
        statement = (
            select(UserSession)
            .options(selectinload(UserSession.user))
            .where(UserSession.token_hash == hash_token(token))
        )
        user_session = (await self._db.execute(statement)).scalar_one_or_none()
        if user_session is None or user_session.revoked_at is not None:
            return None
        if user_session.expires_at <= now:
            return None
        if not _is_usable(user_session.user, now=now):
            return None
        return Principal(
            auth_method=AuthMethod.SESSION, user=user_session.user, session=user_session
        )
