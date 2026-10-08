"""Le greffon d'exemple (`app/plugin_examples/ping`) : inactif par défaut, réel quand on l'active.

Il illustre `docs/extending.md` : un greffon est un dossier avec un attribut `plugin`. Ces tests
prouvent trois choses — rien ne l'active tout seul, `plugins_overridden` l'enregistre bien
(route et réglage par défaut), et le copier sous `app/plugins/` suffit à le faire découvrir.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import AsyncIterator, Iterator
from http import HTTPStatus
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.plugins
from app.core.config import Settings, get_settings
from app.core.plugins import _discover, get_plugins, plugins_overridden
from app.db.session import get_db_session
from app.main import API_PREFIX, create_app
from app.plugin_examples import ping
from tests.conftest import BASE_URL

PING = f"{API_PREFIX}/example/ping"
CREDENTIALS = {"email": "exemple@example.com", "password": "mot-de-passe-solide-2026"}


def test_the_example_is_not_active_by_default() -> None:
    assert "ping" not in [plugin.name for plugin in get_plugins()]
    assert PING not in create_app().openapi()["paths"]


def test_activating_the_example_registers_its_route_and_its_default() -> None:
    with plugins_overridden((ping.plugin,)):
        assert PING in create_app().openapi()["paths"]
        assert Settings().app_name == ping.APP_NAME


def test_the_environment_still_wins_over_the_plugin_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAGENGINE_APP_NAME", "Ma marque")
    with plugins_overridden((ping.plugin,)):
        assert Settings().app_name == "Ma marque"


@pytest.fixture
async def ping_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    with plugins_overridden((ping.plugin,)):
        application = create_app()

        async def _session() -> AsyncIterator[AsyncSession]:
            yield db_session

        application.dependency_overrides[get_db_session] = _session
        async with AsyncClient(transport=ASGITransport(app=application), base_url=BASE_URL) as c:
            yield c


async def test_the_route_answers_a_logged_in_caller_only(ping_client: AsyncClient) -> None:
    assert (await ping_client.get(PING)).status_code == HTTPStatus.UNAUTHORIZED

    registered = await ping_client.post(
        f"{API_PREFIX}/users", json={**CREDENTIALS, "display_name": "Exemple"}
    )
    assert registered.status_code == HTTPStatus.CREATED
    await ping_client.post(f"{API_PREFIX}/sessions", json=CREDENTIALS)
    answer = await ping_client.get(PING)

    assert answer.status_code == HTTPStatus.OK
    assert answer.json() == {"pong": True, "app_name": ping.APP_NAME}


@pytest.fixture
def copied_under_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Copie le dossier du greffon sous un `app/plugins/` simulé, comme le dit la documentation."""
    source = Path(ping.__file__).parent
    shutil.copytree(source, tmp_path / "ping", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(app.plugins, "__path__", [*app.plugins.__path__, str(tmp_path)])
    _discover.cache_clear()
    get_settings.cache_clear()
    yield
    _discover.cache_clear()
    get_settings.cache_clear()
    for name in [m for m in sys.modules if m.startswith("app.plugins.ping")]:
        del sys.modules[name]


def test_copying_the_folder_under_plugins_is_enough_to_be_discovered(
    copied_under_plugins: None,
) -> None:
    names = [plugin.name for plugin in get_plugins()]

    assert "ping" in names
    assert PING in create_app().openapi()["paths"]
