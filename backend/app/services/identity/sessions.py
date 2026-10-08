"""Sessions d'interface : connexion, émission, déconnexion.

Le jeton en clair n'existe **que** dans la valeur de retour de la création et
dans le cookie `HttpOnly` : la base n'en stocke que le SHA-256, si bien qu'une
fuite du dump ne permet pas de rejouer une session.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import TooManyRequestsError
from app.core.rate_limit import (
    RateLimiterUnavailableError,
    get_rate_limiter,
    normalize_client_ip,
)
from app.core.security import (
    generate_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from app.db.models import Session as UserSession
from app.db.models import User
from app.domain.schemas.session import SessionCreate
from app.services.identity.errors import InvalidCredentialsError
from app.services.identity.users import UserService

_INVALID_LOGIN: Final = "Adresse e-mail ou mot de passe incorrect."

_logger = logging.getLogger(__name__)

_LOGIN_IP_KEY_PREFIX: Final = "ratelimit:login:ip"
_LOGIN_ACCOUNT_KEY_PREFIX: Final = "ratelimit:login:account"
"""Clé construite sur l'adresse **soumise**, que le compte existe ou non — le
plafond ne doit jamais devenir un oracle d'énumération (audit sécurité 2026-09,
constat 8 : « l'énumération est aujourd'hui fermée, ne l'ouvre pas »)."""

_LOGIN_RATE_LIMITED: Final = "Trop de tentatives de connexion — réessayez plus tard."
"""Message unique, qu'il s'agisse du plafond par IP ou par compte visé : un
message distinct par compteur laisserait deviner lequel des deux a été
atteint, sans jamais confirmer l'existence d'un compte pour autant (les deux
compteurs se comportent à l'identique, compte réel ou non) — un seul message
retire même cette question."""


@lru_cache(maxsize=1)
def _decoy_password_hash() -> str:
    """Empreinte jetable servant à égaliser le temps de réponse de la connexion.

    Sans elle, une adresse inconnue répondrait sans dérivation argon2id, donc
    bien plus vite qu'une adresse connue : le temps de réponse suffirait à
    énumérer les comptes. Calculée à la première connexion, pas à l'import, pour
    ne pas taxer le démarrage du process ni la collecte des tests.
    """
    return hash_password(secrets.token_urlsafe(32))


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Session fraîchement créée et son jeton en clair, communiqué une seule fois."""

    session: UserSession
    token: str
    user: User


class SessionService:
    """Cas d'usage portant sur la table `sessions`."""

    def __init__(self, db: AsyncSession, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings if settings is not None else get_settings()
        self._users = UserService(db)

    async def login(
        self,
        credentials: SessionCreate,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> IssuedSession:
        """Vérifie les identifiants et ouvre une session — `POST /api/v1/sessions`.

        Raises:
            InvalidCredentialsError: adresse inconnue, mot de passe faux ou
                compte désactivé. Un message unique pour les trois cas : préciser
                lequel reviendrait à confirmer l'existence d'un compte.
            TooManyRequestsError: plafond de connexion par IP ou par compte visé
                atteint (`_enforce_login_rate_limit`, ADR-059).
        """
        await self._enforce_login_rate_limit(email=str(credentials.email), client_ip=ip_address)
        user = await self._users.get_by_email(str(credentials.email))
        password = credentials.password.get_secret_value()

        if user is None or user.password_hash is None:
            verify_password(password, _decoy_password_hash())
            raise InvalidCredentialsError(_INVALID_LOGIN)
        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError(_INVALID_LOGIN)
        if not user.is_active:
            raise InvalidCredentialsError(_INVALID_LOGIN)

        if password_needs_rehash(user.password_hash):
            # Seul instant où le clair est disponible : on en profite pour
            # re-dériver avec les paramètres courants.
            user.password_hash = hash_password(password)

        user.last_login_at = datetime.now(UTC)
        return await self.issue(user, user_agent=user_agent, ip_address=ip_address)

    async def _enforce_login_rate_limit(self, *, email: str, client_ip: str | None) -> None:
        """Plafond dédié à `POST /api/v1/sessions`, par IP **et** par compte visé (ADR-059).

        Le seul garde-fou avant ce correctif était le plafond IP générique du
        middleware (`app.core.rate_limit`, 60/min par défaut) : un bourrage
        d'identifiants réparti sur de nombreuses IP contre un **même** compte
        passait dessous sans jamais être compté par cible (audit sécurité
        2026-09, constat 8). Les deux compteurs sont indépendants et
        complémentaires : l'IP arrête un attaquant qui essaie de nombreux
        comptes depuis une même adresse, le compte arrête un attaquant qui
        distribue ses tentatives sur de nombreuses IP contre une même cible.

        La clé de compte est l'adresse **soumise**, jamais le résultat d'une
        recherche en base : elle se comporte à l'identique qu'un compte existe
        ou non, ce qui ne rouvre pas l'énumération que la connexion ferme déjà
        par ailleurs (`_decoy_password_hash`, message unique).

        **Échec ouvert**, comme le rate limiting anti-rafale
        (`app.core.rate_limit`, ADR-036/040) et à la différence des compteurs de
        dépense réelle (ADR-054, échec fermé) : ce plafond protège un débit
        d'attaque, pas un budget. Un Redis injoignable ne doit pas rendre la
        connexion indisponible pour tout le monde le temps d'une panne — le
        pire résiduel est une fenêtre de quelques secondes sans limitation
        dédiée, avec le plafond IP générique toujours actif en amont.

        Raises:
            TooManyRequestsError: plafond IP ou compte atteint.
        """
        limiter = get_rate_limiter(self._settings)
        window = self._settings.login_rate_limit_window_seconds
        ip_identity = normalize_client_ip(client_ip) if client_ip is not None else "unknown"
        account_identity = email.strip().lower()
        try:
            ip_outcome = await limiter.hit(
                f"{_LOGIN_IP_KEY_PREFIX}:{ip_identity}",
                limit=self._settings.login_rate_limit_ip_attempts,
                window_seconds=window,
            )
            if ip_outcome.allowed:
                account_outcome = await limiter.hit(
                    f"{_LOGIN_ACCOUNT_KEY_PREFIX}:{account_identity}",
                    limit=self._settings.login_rate_limit_account_attempts,
                    window_seconds=window,
                )
            else:
                account_outcome = None
        except RateLimiterUnavailableError:
            _logger.warning(
                "Compteur de connexion injoignable — tentative laissée passer (fail-open)."
            )
            return
        if not ip_outcome.allowed:
            _logger.warning("Plafond de connexion par IP atteint.")
            raise TooManyRequestsError(
                _LOGIN_RATE_LIMITED,
                extensions={"retry_after_seconds": ip_outcome.retry_after_seconds},
            )
        if account_outcome is not None and not account_outcome.allowed:
            _logger.warning("Plafond de connexion par compte visé atteint.")
            raise TooManyRequestsError(
                _LOGIN_RATE_LIMITED,
                extensions={"retry_after_seconds": account_outcome.retry_after_seconds},
            )

    async def revoke(self, session: UserSession) -> UserSession:
        """Révoque une session — `DELETE /api/v1/sessions/current`.

        Idempotent : révoquer deux fois ne déplace pas l'horodatage initial, qui
        reste la trace auditable de la déconnexion.
        """
        if session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            await self._db.flush([session])
        return session

    async def issue(
        self, user: User, *, user_agent: str | None, ip_address: str | None
    ) -> IssuedSession:
        """Tire un jeton, persiste son empreinte et renvoie le clair une seule fois.

        Seul point d'émission d'une session : la connexion l'appelle, et un
        greffon qui ouvre une session par un autre chemin passe par lui aussi.
        """
        token = generate_token()
        expires_at = datetime.now(UTC) + timedelta(hours=self._settings.session_ttl_hours)
        if user.expires_at is not None:
            # Une session ne survit jamais à l'espace invité qu'elle dessert.
            expires_at = min(expires_at, user.expires_at)

        session = UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self._db.add(session)
        await self._db.flush([session])
        return IssuedSession(session=session, token=token, user=user)
