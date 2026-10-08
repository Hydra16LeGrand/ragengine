"""Résolution du principal : clé API, cookie de session, anonyme.

C'est le test de l'ordre documenté en §6 de `docs/domain-model.md`, et surtout
de son asymétrie : une clé API invalide est une erreur, un cookie périmé ne l'est
pas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_token
from app.db.models import ApiKey, User
from app.domain.schemas.api_key import ApiKeyCreate
from app.domain.schemas.principal import AuthMethod
from app.domain.schemas.session import SessionCreate
from app.domain.schemas.user import UserCreate
from app.services.identity.api_keys import ApiKeyService
from app.services.identity.errors import InvalidCredentialsError
from app.services.identity.principal import ANONYMOUS, PrincipalResolver
from app.services.identity.sessions import IssuedSession, SessionService
from app.services.identity.users import UserService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


async def _account(db: AsyncSession) -> tuple[User, str]:
    """Compte réel et son adresse."""
    email = factories.unique_email("principal")
    user = await UserService(db).register(
        UserCreate(email=email, password=SecretStr(PASSWORD), display_name="Amara")
    )
    return user, email


async def _logged_in(db: AsyncSession) -> IssuedSession:
    """Compte réel connecté, avec le jeton de session en clair."""
    _, email = await _account(db)
    return await SessionService(db).login(SessionCreate(email=email, password=SecretStr(PASSWORD)))


async def _api_key(db: AsyncSession, user: User) -> tuple[ApiKey, str]:
    """Clé API active et son jeton en clair."""
    issued = await ApiKeyService(db).create(user, ApiKeyCreate(name="CI"))
    return issued.api_key, issued.token


async def test_no_credential_yields_the_anonymous_principal(db_session: AsyncSession) -> None:
    """Sans identifiant, la résolution ne lève pas : elle rend l'anonyme."""
    principal = await PrincipalResolver(db_session).resolve()

    assert principal == ANONYMOUS
    assert principal.auth_method is AuthMethod.ANONYMOUS
    assert principal.is_authenticated is False
    assert principal.role is None


async def test_a_valid_api_key_identifies_its_owner(db_session: AsyncSession) -> None:
    """Cas nominal du client machine."""
    user, _ = await _account(db_session)
    _, token = await _api_key(db_session, user)

    principal = await PrincipalResolver(db_session).resolve(authorization=f"Bearer {token}")

    assert principal.auth_method is AuthMethod.API_KEY
    assert principal.user is not None
    assert principal.user.id == user.id


async def test_using_an_api_key_records_its_last_use(db_session: AsyncSession) -> None:
    """`last_used_at` permet de repérer une clé dormante ou compromise."""
    user, _ = await _account(db_session)
    api_key, token = await _api_key(db_session, user)
    assert api_key.last_used_at is None

    await PrincipalResolver(db_session).resolve(authorization=f"Bearer {token}")

    assert api_key.last_used_at is not None


async def test_an_unknown_api_key_is_rejected(db_session: AsyncSession) -> None:
    """Une clé inconnue ne dégrade pas en anonyme : le client doit la corriger."""
    with pytest.raises(InvalidCredentialsError):
        await PrincipalResolver(db_session).resolve(
            authorization=f"Bearer rag_test_{generate_token()}"
        )


async def test_a_revoked_api_key_is_rejected(db_session: AsyncSession) -> None:
    """La révocation est effective immédiatement, sans attendre d'expiration."""
    user, _ = await _account(db_session)
    api_key, token = await _api_key(db_session, user)
    await ApiKeyService(db_session).revoke(user, api_key.id)

    with pytest.raises(InvalidCredentialsError):
        await PrincipalResolver(db_session).resolve(authorization=f"Bearer {token}")


async def test_an_expired_api_key_is_rejected(db_session: AsyncSession) -> None:
    """L'échéance est vérifiée à chaque requête, pas seulement à la création."""
    user, _ = await _account(db_session)
    api_key, token = await _api_key(db_session, user)
    api_key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    with pytest.raises(InvalidCredentialsError):
        await PrincipalResolver(db_session).resolve(authorization=f"Bearer {token}")


async def test_a_key_of_a_deactivated_account_is_rejected(db_session: AsyncSession) -> None:
    """Désactiver un compte coupe aussi ses accès machine."""
    user, _ = await _account(db_session)
    _, token = await _api_key(db_session, user)
    user.is_active = False
    await db_session.flush()

    with pytest.raises(InvalidCredentialsError):
        await PrincipalResolver(db_session).resolve(authorization=f"Bearer {token}")


@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "Bearer   ", "jeton-nu"])
async def test_a_malformed_authorization_header_is_rejected(
    db_session: AsyncSession, header: str
) -> None:
    """Un en-tête illisible est une erreur du client, pas une requête anonyme."""
    with pytest.raises(InvalidCredentialsError):
        await PrincipalResolver(db_session).resolve(authorization=header)


@pytest.mark.parametrize("header", [None, "", "   "])
async def test_an_absent_authorization_header_is_not_an_error(
    db_session: AsyncSession, header: str | None
) -> None:
    """Absence d'en-tête : on passe au cookie, puis à l'anonyme."""
    assert await PrincipalResolver(db_session).resolve(authorization=header) == ANONYMOUS


async def test_the_scheme_is_case_insensitive(db_session: AsyncSession) -> None:
    """La RFC 9110 rend le schéma insensible à la casse."""
    user, _ = await _account(db_session)
    _, token = await _api_key(db_session, user)

    principal = await PrincipalResolver(db_session).resolve(authorization=f"bearer {token}")

    assert principal.user is not None
    assert principal.user.id == user.id


async def test_a_valid_session_cookie_identifies_its_owner(db_session: AsyncSession) -> None:
    """Cas nominal de l'interface."""
    issued = await _logged_in(db_session)

    principal = await PrincipalResolver(db_session).resolve(session_token=issued.token)

    assert principal.auth_method is AuthMethod.SESSION
    assert principal.user is not None
    assert principal.user.id == issued.user.id
    assert principal.session is not None


async def test_an_expired_session_falls_back_to_anonymous(db_session: AsyncSession) -> None:
    """Un cookie périmé est un état normal du navigateur, pas une erreur."""
    issued = await _logged_in(db_session)
    issued.session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    assert await PrincipalResolver(db_session).resolve(session_token=issued.token) == ANONYMOUS


async def test_a_revoked_session_falls_back_to_anonymous(db_session: AsyncSession) -> None:
    """Après déconnexion, le même cookie ne vaut plus rien."""
    issued = await _logged_in(db_session)
    await SessionService(db_session).revoke(issued.session)

    assert await PrincipalResolver(db_session).resolve(session_token=issued.token) == ANONYMOUS


async def test_an_unknown_session_token_falls_back_to_anonymous(db_session: AsyncSession) -> None:
    """Un cookie forgé ne provoque pas de 401 sur les routes publiques."""
    resolved = await PrincipalResolver(db_session).resolve(session_token=generate_token())

    assert resolved == ANONYMOUS


async def test_a_session_of_a_deactivated_account_falls_back_to_anonymous(
    db_session: AsyncSession,
) -> None:
    """La désactivation logique coupe aussi les sessions en cours."""
    issued = await _logged_in(db_session)
    issued.user.is_active = False
    await db_session.flush()

    assert await PrincipalResolver(db_session).resolve(session_token=issued.token) == ANONYMOUS


async def test_an_expired_guest_no_longer_authenticates(db_session: AsyncSession) -> None:
    """Un espace échu n'authentifie plus, même avant le passage de la purge."""
    issued = await factories.ephemeral_session(db_session)
    issued.user.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    assert await PrincipalResolver(db_session).resolve(session_token=issued.token) == ANONYMOUS


async def test_a_guest_session_identifies_a_guest_principal(db_session: AsyncSession) -> None:
    """L'espace invité est un principal de premier rang, pas un cas d'erreur."""
    issued = await factories.ephemeral_session(db_session)

    principal = await PrincipalResolver(db_session).resolve(session_token=issued.token)

    assert principal.is_authenticated is True
    assert principal.is_guest is True


async def test_the_api_key_wins_over_the_cookie(db_session: AsyncSession) -> None:
    """L'en-tête est prioritaire : c'est lui, et lui seul, qui est évalué."""
    user, _ = await _account(db_session)
    _, token = await _api_key(db_session, user)
    cookie = await _logged_in(db_session)

    principal = await PrincipalResolver(db_session).resolve(
        authorization=f"Bearer {token}", session_token=cookie.token
    )

    assert principal.auth_method is AuthMethod.API_KEY
    assert principal.user is not None
    assert principal.user.id == user.id


async def test_an_invalid_api_key_never_falls_back_to_the_cookie(
    db_session: AsyncSession,
) -> None:
    """Sans cette règle, une clé cassée passerait inaperçue derrière un cookie valide."""
    cookie = await _logged_in(db_session)

    with pytest.raises(InvalidCredentialsError):
        await PrincipalResolver(db_session).resolve(
            authorization=f"Bearer rag_test_{generate_token()}", session_token=cookie.token
        )
