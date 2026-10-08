"""Connexion, ouverture d'espace invité et révocation de session."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit as rate_limit_module
from app.core.config import Settings
from app.core.errors import TooManyRequestsError
from app.core.rate_limit import RateLimiterUnavailableError
from app.core.security import hash_token
from app.db.models import Session as UserSession
from app.domain.schemas.session import SessionCreate
from app.domain.schemas.user import UserCreate
from app.services.identity.errors import InvalidCredentialsError
from app.services.identity.sessions import IssuedSession, SessionService
from app.services.identity.users import UserService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


async def _register(db: AsyncSession, email: str | None = None) -> str:
    """Inscrit un compte et renvoie son adresse."""
    address = email or factories.unique_email("connexion")
    await UserService(db).register(
        UserCreate(email=address, password=SecretStr(PASSWORD), display_name="Amara")
    )
    return address


async def test_login_issues_a_session_whose_token_is_only_stored_hashed(
    db_session: AsyncSession,
) -> None:
    """Le clair sort une seule fois ; la base n'en garde que le SHA-256."""
    email = await _register(db_session)

    issued = await SessionService(db_session).login(
        SessionCreate(email=email, password=SecretStr(PASSWORD)),
        user_agent="pytest",
        ip_address="127.0.0.1",
    )

    assert issued.token
    assert issued.session.token_hash == hash_token(issued.token)
    assert issued.session.revoked_at is None
    assert issued.session.expires_at > datetime.now(UTC)


async def test_login_records_the_last_connection(db_session: AsyncSession) -> None:
    """`last_login_at` alimente les réglages de sécurité de l'interface."""
    email = await _register(db_session)

    issued = await SessionService(db_session).login(
        SessionCreate(email=email, password=SecretStr(PASSWORD))
    )

    assert issued.user.last_login_at is not None


async def test_login_refuses_a_wrong_password(db_session: AsyncSession) -> None:
    """Mot de passe faux : `401`, sans indiquer lequel des deux champs est en cause."""
    email = await _register(db_session)

    with pytest.raises(InvalidCredentialsError):
        await SessionService(db_session).login(
            SessionCreate(email=email, password=SecretStr("ce-n-est-pas-le-bon"))
        )


async def test_login_refuses_an_unknown_email(db_session: AsyncSession) -> None:
    """Adresse inconnue : même erreur, pour ne pas énumérer les comptes."""
    with pytest.raises(InvalidCredentialsError):
        await SessionService(db_session).login(
            SessionCreate(email=factories.unique_email("fantome"), password=SecretStr(PASSWORD))
        )


async def test_login_refuses_a_deactivated_account(db_session: AsyncSession) -> None:
    """La désactivation logique coupe l'accès sans supprimer la ligne."""
    email = await _register(db_session)
    user = await UserService(db_session).get_by_email(email)
    assert user is not None
    user.is_active = False
    await db_session.flush()

    with pytest.raises(InvalidCredentialsError):
        await SessionService(db_session).login(
            SessionCreate(email=email, password=SecretStr(PASSWORD))
        )


async def test_a_session_never_outlives_an_expiring_account(db_session: AsyncSession) -> None:
    """`issue` borne la session à l'échéance du compte qu'elle dessert, s'il en a une."""
    account = factories.make_guest(ttl_hours=1)
    db_session.add(account)
    await db_session.flush([account])

    issued = await SessionService(db_session, Settings(SESSION_TTL_HOURS=720)).issue(
        account, user_agent=None, ip_address=None
    )

    assert account.expires_at is not None
    assert issued.session.expires_at <= account.expires_at


async def test_revocation_is_immediate_and_idempotent(db_session: AsyncSession) -> None:
    """Un jeton opaque se révoque en base, sans attendre son expiration."""
    email = await _register(db_session)
    service = SessionService(db_session)
    issued = await service.login(SessionCreate(email=email, password=SecretStr(PASSWORD)))

    await service.revoke(issued.session)
    first_revocation = issued.session.revoked_at
    await service.revoke(issued.session)

    assert first_revocation is not None
    assert issued.session.revoked_at == first_revocation


async def test_the_plaintext_token_is_nowhere_in_the_sessions_table(
    db_session: AsyncSession,
) -> None:
    """Une fuite du dump ne permet pas de rejouer une session."""
    email = await _register(db_session)
    issued = await SessionService(db_session).login(
        SessionCreate(email=email, password=SecretStr(PASSWORD))
    )

    rows = (await db_session.execute(select(UserSession))).scalars().all()

    assert all(issued.token not in row.token_hash for row in rows)


# ─── Plafond global de créations d'espaces invités (ADR-054) ────────────────


# ─── Rotation de session à l'élévation (audit sécurité 2026-09, constat 7) ──


# ─── Plafond dédié sur la connexion (audit sécurité 2026-09, constat 8) ─────


async def _attempt(service: SessionService, *, email: str, ip_address: str) -> IssuedSession:
    """Une tentative de connexion avec un mot de passe quelconque — peuple le compteur.

    Ne capture rien : `InvalidCredentialsError` (mot de passe faux, cas normal
    ici) et `TooManyRequestsError` (plafond atteint) remontent telle quelle, à
    l'appelant de choisir laquelle attendre avec `pytest.raises`.
    """
    return await service.login(
        SessionCreate(email=email, password=SecretStr("peu-importe-2026")),
        ip_address=ip_address,
    )


async def test_login_is_capped_per_ip_across_distinct_accounts(db_session: AsyncSession) -> None:
    """Un bourrage visant des comptes distincts depuis une même IP est arrêté par IP."""
    settings = Settings(LOGIN_RATE_LIMIT_IP_ATTEMPTS=2, LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS=100)
    service = SessionService(db_session, settings)
    client_ip = "203.0.113.50"

    for _ in range(2):
        with pytest.raises(InvalidCredentialsError):
            await _attempt(service, email=factories.unique_email("ip-cap"), ip_address=client_ip)

    with pytest.raises(TooManyRequestsError) as refused:
        await _attempt(service, email=factories.unique_email("ip-cap"), ip_address=client_ip)

    retry_after = refused.value.extensions["retry_after_seconds"]
    assert isinstance(retry_after, int)
    assert 0 < retry_after <= settings.login_rate_limit_window_seconds


async def test_login_is_capped_per_account_across_distinct_ips(db_session: AsyncSession) -> None:
    """Un bourrage distribué sur de nombreuses IP contre un même compte est arrêté par compte,
    que ce compte existe ou non — la réponse ne le révèle jamais."""
    settings = Settings(LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS=2, LOGIN_RATE_LIMIT_IP_ATTEMPTS=100)
    service = SessionService(db_session, settings)
    target_email = factories.unique_email("account-cap")

    for index in range(2):
        with pytest.raises(InvalidCredentialsError):
            await _attempt(service, email=target_email, ip_address=f"198.51.100.{index}")

    with pytest.raises(TooManyRequestsError) as refused:
        await _attempt(service, email=target_email, ip_address="198.51.100.99")

    retry_after = refused.value.extensions["retry_after_seconds"]
    assert isinstance(retry_after, int)
    assert 0 < retry_after <= settings.login_rate_limit_window_seconds
    # Un autre compte, depuis une IP neuve, n'est pas affecté : les compteurs
    # sont bien indépendants par cible.
    with pytest.raises(InvalidCredentialsError):
        await _attempt(
            service, email=factories.unique_email("indemne"), ip_address="198.51.100.100"
        )


async def test_a_successful_login_is_not_blocked_by_an_unrelated_counter(
    db_session: AsyncSession,
) -> None:
    """Le compte légitime, jamais bourré, se connecte normalement malgré le plafond dédié."""
    settings = Settings(LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS=2, LOGIN_RATE_LIMIT_IP_ATTEMPTS=2)
    email = await _register(db_session)

    issued = await SessionService(db_session, settings).login(
        SessionCreate(email=email, password=SecretStr(PASSWORD)), ip_address="192.0.2.200"
    )

    assert issued.token


async def test_login_rate_limit_fails_open_when_the_counter_is_unreachable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis injoignable : la connexion légitime continue de fonctionner (échec ouvert,
    à la différence des compteurs de dépense réelle, ADR-054)."""

    class _UnreachableLimiter:
        async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
            raise RateLimiterUnavailableError("Connection refused")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(rate_limit_module, "_limiter", _UnreachableLimiter())
    email = await _register(db_session)

    issued = await SessionService(db_session).login(
        SessionCreate(email=email, password=SecretStr(PASSWORD)), ip_address="192.0.2.201"
    )

    assert issued.token
