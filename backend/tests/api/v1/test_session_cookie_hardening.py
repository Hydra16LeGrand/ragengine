"""Cookie de session en environnement exposé (audit sécurité 2026-09, constat 4, ADR-064).

Distinct de `test_identity_routes.py::test_the_session_cookie_is_hardened`, qui tourne en
développement (pas de préfixe `__Host-` attendu) : ce module boote l'application avec
`RAGENGINE_ENV=staging` pour prouver, sur un aller-retour HTTP réel, que le cookie posé porte
bien le préfixe et que le serveur qui l'a émis sait aussi le relire — pas seulement que
`Settings.session_cookie_effective_name` calcule la bonne valeur en isolation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes import api_keys, auth
from app.core.config import get_settings
from app.db.session import get_db_session
from app.main import API_PREFIX, create_app
from tests.db import factories

_HTTPS_BASE_URL = "https://testserver"
"""Le cookie posé porte `Secure` (environnement exposé) : `http.cookiejar`, sur lequel
httpx s'appuie, ne le rejoue jamais sur un schéma `http://` — même dans un test, il faut
un client dont l'URL de base est bien `https://` pour observer l'aller-retour réel."""

PASSWORD = "mot-de-passe-solide-2026"
PREFIXED_COOKIE_NAME = "__Host-ragengine_session"


@pytest.fixture
def app(_isolated_environment: None, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """`RAGENGINE_ENV=staging` : les trois secrets requis par
    `_forbid_dev_secret_outside_development` doivent être surchargés pour que
    l'application démarre."""
    monkeypatch.setenv("RAGENGINE_ENV", "staging")
    monkeypatch.setenv("RAGENGINE_SECRET_KEY", "une-vraie-cle-de-signature-de-session")
    monkeypatch.setenv("POSTGRES_PASSWORD", "un-vrai-mot-de-passe-postgres")
    monkeypatch.setenv("RAGFLOW_BOOTSTRAP_PASSWORD", "un-vrai-mot-de-passe-ragflow")
    monkeypatch.setenv("RAGENGINE_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def exposed_app(app: FastAPI, db_session: AsyncSession) -> FastAPI:
    """Même montage que `tests/api/v1/conftest.py::identity_app` (identité seule, cette
    suite n'a besoin ni de documents ni de stockage), sur l'`app` de staging ci-dessus
    plutôt que sur celle, non exposée, de `tests/conftest.py`."""

    async def _test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(api_keys.router, prefix=API_PREFIX)
    app.dependency_overrides[get_db_session] = _test_session
    return app


@pytest.fixture
async def exposed_client(exposed_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=exposed_app)
    async with AsyncClient(transport=transport, base_url=_HTTPS_BASE_URL) as client:
        yield client


async def test_login_emits_the_host_prefixed_cookie_and_the_server_reads_it_back(
    exposed_client: AsyncClient,
) -> None:
    """Preuve d'un aller-retour réel : le cookie posé au login est celui que le serveur
    accepte ensuite pour authentifier — pas deux noms qui divergent silencieusement."""
    email = factories.unique_email("staging-cookie")
    payload = {"email": email, "password": PASSWORD, "display_name": "Amara"}
    created = await exposed_client.post(f"{API_PREFIX}/users", json=payload)
    assert created.status_code == HTTPStatus.CREATED

    session = await exposed_client.post(
        f"{API_PREFIX}/sessions", json={"email": email, "password": PASSWORD}
    )
    assert session.status_code == HTTPStatus.CREATED
    set_cookie = session.headers["set-cookie"]
    assert f"{PREFIXED_COOKIE_NAME}=" in set_cookie
    assert "ragengine_session=" not in set_cookie.replace(PREFIXED_COOKIE_NAME, "")
    assert "secure" in set_cookie.lower()

    # httpx conserve le cookie posé et le rejoue : la lecture serveur doit le reconnaître.
    assert PREFIXED_COOKIE_NAME in exposed_client.cookies
    me = await exposed_client.get(f"{API_PREFIX}/me")
    assert me.status_code == HTTPStatus.OK
    assert me.json()["user"]["email"] == email
