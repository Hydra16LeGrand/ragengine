"""Parcours HTTP complets d'identité : inscription, session, invité, clés API.

Ces tests décrivent le contrat tel que le frontend le consommera : codes de
statut, cookie, et surtout le `type` des Problem Details, qui est ce sur quoi
l'interface branche ses parcours d'élévation."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.errors import PROBLEM_MEDIA_TYPE
from app.main import API_PREFIX
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


COOKIE_NAME = "ragengine_session"


def _registration(email: str | None = None) -> dict[str, str]:
    """Corps d'inscription valide."""
    return {
        "email": email or factories.unique_email("http"),
        "password": PASSWORD,
        "display_name": "Amara",
    }


async def _register_and_log_in(client: AsyncClient) -> dict[str, Any]:
    """Inscrit un compte puis ouvre une session ; renvoie le principal courant."""
    payload = _registration()
    created = await client.post(f"{API_PREFIX}/users", json=payload)
    assert created.status_code == HTTPStatus.CREATED
    session = await client.post(
        f"{API_PREFIX}/sessions", json={"email": payload["email"], "password": PASSWORD}
    )
    assert session.status_code == HTTPStatus.CREATED
    return dict(session.json())


async def test_registration_returns_the_account_without_any_secret(
    identity_client: AsyncClient,
) -> None:
    """`POST /users` : `201`, et aucune trace du mot de passe dans la réponse."""
    response = await identity_client.post(f"{API_PREFIX}/users", json=_registration())

    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["is_guest"] is False
    assert body["role"] == "member"
    assert "password" not in body
    assert "password_hash" not in body


async def test_registering_a_taken_email_answers_409(identity_client: AsyncClient) -> None:
    """Adresse déjà prise : `409` en Problem Details."""
    payload = _registration()
    await identity_client.post(f"{API_PREFIX}/users", json=payload)

    response = await identity_client.post(f"{API_PREFIX}/users", json=payload)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["type"] == "/problems/conflict"


async def test_a_short_password_is_refused_before_reaching_the_service(
    identity_client: AsyncClient,
) -> None:
    """La politique de mot de passe est portée par le schéma : `422`."""
    response = await identity_client.post(
        f"{API_PREFIX}/users", json=_registration() | {"password": "court"}
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


async def test_the_full_session_lifecycle(identity_client: AsyncClient) -> None:
    """Inscription → connexion → accès authentifié → déconnexion → cookie mort."""
    principal = await _register_and_log_in(identity_client)
    assert principal["auth_method"] == "session"
    assert principal["is_guest"] is False
    token = identity_client.cookies[COOKIE_NAME]

    authenticated = await identity_client.get(f"{API_PREFIX}/me")
    assert authenticated.status_code == HTTPStatus.OK
    assert authenticated.json()["user"]["email"] == principal["user"]["email"]

    logout = await identity_client.delete(f"{API_PREFIX}/sessions/current")
    assert logout.status_code == HTTPStatus.NO_CONTENT
    assert COOKIE_NAME not in identity_client.cookies

    # Le cookie est rejoué de force : la session est révoquée en base, pas
    # seulement effacée du navigateur.
    identity_client.cookies.set(COOKIE_NAME, token)
    refused = await identity_client.get(f"{API_PREFIX}/me")
    assert refused.status_code == HTTPStatus.UNAUTHORIZED
    assert refused.json()["type"] == "/problems/authentication-required"


async def test_the_session_cookie_is_hardened(identity_client: AsyncClient) -> None:
    """`HttpOnly` contre l'exfiltration par XSS, `SameSite=Lax` contre le CSRF."""
    payload = _registration()
    await identity_client.post(f"{API_PREFIX}/users", json=payload)

    response = await identity_client.post(
        f"{API_PREFIX}/sessions", json={"email": payload["email"], "password": PASSWORD}
    )

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    # `Secure` suit `SESSION_COOKIE_SECURE`, faux par défaut en développement.
    assert "secure" not in cookie


async def test_a_wrong_password_answers_401(identity_client: AsyncClient) -> None:
    """Identifiants refusés : `401` typé `invalid-credentials`."""
    payload = _registration()
    await identity_client.post(f"{API_PREFIX}/users", json=payload)

    response = await identity_client.post(
        f"{API_PREFIX}/sessions", json={"email": payload["email"], "password": "pas-le-bon-2026"}
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "/problems/invalid-credentials"


async def test_login_beyond_the_ip_cap_answers_429_with_retry_after(
    identity_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-059 (audit sécurité 2026-09, constat 8) : plafond dédié à la connexion — `429`
    Problem Details avec `Retry-After`, sans jamais révéler si le compte visé existe."""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_IP_ATTEMPTS", "2")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS", "100")
    get_settings.cache_clear()

    for _ in range(2):
        response = await identity_client.post(
            f"{API_PREFIX}/sessions",
            json={"email": factories.unique_email("http-ip-cap"), "password": "peu-importe-2026"},
        )
        assert response.status_code == HTTPStatus.UNAUTHORIZED

    refused = await identity_client.post(
        f"{API_PREFIX}/sessions",
        json={"email": factories.unique_email("http-ip-cap"), "password": "peu-importe-2026"},
    )

    assert refused.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert refused.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    retry_after = int(refused.headers["retry-after"])
    assert retry_after > 0
    assert refused.json()["retry_after_seconds"] == retry_after


async def test_me_requires_authentication(identity_client: AsyncClient) -> None:
    """Sans identifiant, `GET /me` déclenche le parcours d'élévation."""
    response = await identity_client.get(f"{API_PREFIX}/me")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "/problems/authentication-required"


async def test_logging_out_twice_is_not_an_error(identity_client: AsyncClient) -> None:
    """Idempotence : le second appel ne trouve plus de session, et c'est normal."""
    await _register_and_log_in(identity_client)

    first = await identity_client.delete(f"{API_PREFIX}/sessions/current")
    second = await identity_client.delete(f"{API_PREFIX}/sessions/current")

    assert first.status_code == HTTPStatus.NO_CONTENT
    assert second.status_code == HTTPStatus.UNAUTHORIZED


async def test_api_keys_require_authentication(identity_client: AsyncClient) -> None:
    """Un anonyme ne liste pas de clés."""
    response = await identity_client.get(f"{API_PREFIX}/api-keys")

    assert response.status_code == HTTPStatus.UNAUTHORIZED


async def test_the_api_key_lifecycle(identity_client: AsyncClient) -> None:
    """Création (jeton une fois) → liste (jamais le jeton) → révocation."""
    await _register_and_log_in(identity_client)

    created = await identity_client.post(f"{API_PREFIX}/api-keys", json={"name": "CI"})
    assert created.status_code == HTTPStatus.CREATED
    key = created.json()
    assert key["token"].startswith("rag_")
    assert key["prefix"] == key["token"][:12]

    listed = await identity_client.get(f"{API_PREFIX}/api-keys")
    assert listed.status_code == HTTPStatus.OK
    page = listed.json()
    assert [item["id"] for item in page["data"]] == [key["id"]]
    assert page["next_cursor"] is None
    assert "token" not in page["data"][0]

    revoked = await identity_client.delete(f"{API_PREFIX}/api-keys/{key['id']}")
    assert revoked.status_code == HTTPStatus.NO_CONTENT

    after = await identity_client.get(f"{API_PREFIX}/api-keys")
    assert after.json()["data"][0]["revoked_at"] is not None


async def test_an_api_key_authenticates_a_machine_client(identity_client: AsyncClient) -> None:
    """Le développeur s'authentifie sans cookie, par en-tête `Authorization`."""
    await _register_and_log_in(identity_client)
    token = (await identity_client.post(f"{API_PREFIX}/api-keys", json={"name": "CI"})).json()[
        "token"
    ]
    identity_client.cookies.clear()

    response = await identity_client.get(
        f"{API_PREFIX}/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["auth_method"] == "api_key"


async def test_a_revoked_api_key_is_refused(identity_client: AsyncClient) -> None:
    """La révocation coupe l'accès immédiatement."""
    await _register_and_log_in(identity_client)
    key = (await identity_client.post(f"{API_PREFIX}/api-keys", json={"name": "CI"})).json()
    await identity_client.delete(f"{API_PREFIX}/api-keys/{key['id']}")
    identity_client.cookies.clear()

    response = await identity_client.get(
        f"{API_PREFIX}/me", headers={"Authorization": f"Bearer {key['token']}"}
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "/problems/invalid-credentials"


@pytest.mark.parametrize("header", ["Bearer rag_test_inconnu", "Basic abc", "n-importe-quoi"])
async def test_an_invalid_authorization_header_answers_401(
    identity_client: AsyncClient, header: str
) -> None:
    """Une clé présentée et rejetée ne dégrade jamais en anonyme."""
    response = await identity_client.get(f"{API_PREFIX}/me", headers={"Authorization": header})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "/problems/invalid-credentials"


async def test_a_stale_cookie_does_not_break_public_browsing(
    identity_client: AsyncClient,
) -> None:
    """Un cookie inconnu redevient anonyme : `401` d'élévation, pas d'erreur d'identifiant."""
    identity_client.cookies.set(COOKIE_NAME, "cookie-perime-quelconque")

    response = await identity_client.get(f"{API_PREFIX}/me")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "/problems/authentication-required"
