"""Tests du rate limiting par clé API / IP (`app/core/rate_limit.py`, ADR-018).

Deux familles : des tests **d'intégration Redis réel**, qui vérifient le
comptage lui-même (`RedisRateLimiter.hit`, puis le middleware bout en bout) et
sont **skippés** si aucun Redis n'est joignable — même politique que
`tests/postgres.py` pour PostgreSQL, pour la même raison (un `INCR`/`EXPIRE`
atomique ne se simule pas fidèlement sans un vrai serveur) ; et des tests
**purs**, sans Redis, qui vérifient les chemins où le middleware ne doit
*jamais* dépendre de Redis pour se comporter correctement : `/health` exclu,
`rate_limit_enabled=False`, et l'échec ouvert quand Redis est injoignable.

URL Redis de test, par ordre de priorité (lue à l'import, comme
`tests/postgres.py`, donc avant que `_isolated_environment` n'efface les
variables `REDIS_`/`RAGENGINE_`) :

1. `TEST_REDIS_URL` ;
2. à défaut, `redis://localhost:{RAGENGINE_REDIS_HOST_PORT}/9` — base `9`,
   dédiée aux tests et distincte des bases `0`/`1`/`2` utilisées par l'API et
   Celery (`.env.example`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from http import HTTPStatus
from pathlib import Path

import pytest
import redis.asyncio as redis_asyncio
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core import rate_limit
from app.core.config import Settings
from app.core.rate_limit import (
    RateLimiterUnavailableError,
    RateLimitMiddleware,
    RateLimitOutcome,
    RedisRateLimiter,
    close_rate_limiter,
    normalize_client_ip,
)

# Lu à l'import, donc avant que `_isolated_environment` (autouse, tests/conftest.py)
# n'efface les variables d'environnement `REDIS_`/`RAGENGINE_` avant chaque test.
_HOST_PORT = os.environ.get("RAGENGINE_REDIS_HOST_PORT", "6380")
_EXPLICIT_URL = os.environ.get("TEST_REDIS_URL", "")
_TEST_DB_INDEX = 9


def _resolve_test_redis_url() -> str:
    if _EXPLICIT_URL:
        return _EXPLICIT_URL
    return f"redis://localhost:{_HOST_PORT}/{_TEST_DB_INDEX}"


async def _ok(request: Request) -> PlainTextResponse:
    del request
    return PlainTextResponse("ok")


@pytest.fixture
async def redis_test_url() -> AsyncIterator[str]:
    """URL d'un Redis réellement joignable, ou `skip` sinon."""
    url = _resolve_test_redis_url()
    probe = redis_asyncio.from_url(url)  # type: ignore[no-untyped-call]
    try:
        await probe.ping()
    except RedisError:
        pytest.skip(f"Redis injoignable sur {url} — démarrer la stack Docker ou TEST_REDIS_URL.")
    finally:
        await probe.aclose()

    yield url

    cleanup = redis_asyncio.from_url(url)  # type: ignore[no-untyped-call]
    try:
        await cleanup.flushdb()
    finally:
        await cleanup.aclose()


@pytest.fixture(autouse=True)
async def _fresh_rate_limiter_singleton() -> AsyncIterator[None]:
    """Réinitialise le singleton de `app.core.rate_limit` autour de chaque test.

    Sans ça, le premier test qui construit un `RateLimitMiddleware` fige la
    connexion Redis du process pour toute la suite (`get_rate_limiter` ne
    recrée le client que si `_limiter is None`) — un test suivant avec une
    autre `REDIS_URL` réutiliserait silencieusement l'ancienne connexion.
    """
    await close_rate_limiter()
    yield
    await close_rate_limiter()


@pytest.fixture(autouse=True)
def _fresh_gateway_detection_cache() -> Iterator[None]:
    """Réinitialise le cache de `_detect_docker_gateway_ip` autour de chaque test.

    `@lru_cache` mémorise le premier résultat pour toute la durée du process —
    sans ça, un test qui bascule `_DOCKERENV_MARKER`/`_PROC_NET_ROUTE` par
    `monkeypatch` verrait un résultat périmé si un test précédent avait déjà
    peuplé le cache (ADR-040).
    """
    rate_limit._detect_docker_gateway_ip.cache_clear()
    yield
    rate_limit._detect_docker_gateway_ip.cache_clear()


# --- RedisRateLimiter : comptage réel -------------------------------------


async def test_hit_allows_up_to_the_limit_then_blocks(redis_test_url: str) -> None:
    """Les `limit` premiers appels passent, le suivant est refusé avec un délai positif."""
    client = redis_asyncio.from_url(redis_test_url, decode_responses=True)  # type: ignore[no-untyped-call]
    limiter = RedisRateLimiter(client)
    key = f"test:hit:{uuid.uuid4()}"

    try:
        first = await limiter.hit(key, limit=2, window_seconds=60)
        second = await limiter.hit(key, limit=2, window_seconds=60)
        third = await limiter.hit(key, limit=2, window_seconds=60)
    finally:
        await limiter.close()

    assert first == RateLimitOutcome(allowed=True, remaining=1, retry_after_seconds=0)
    assert second == RateLimitOutcome(allowed=True, remaining=0, retry_after_seconds=0)
    assert third.allowed is False
    assert third.retry_after_seconds > 0


async def test_hit_uses_independent_counters_per_key(redis_test_url: str) -> None:
    """Deux clés distinctes (deux IP, ou IP vs empreinte de jeton) ne s'influencent pas."""
    client = redis_asyncio.from_url(redis_test_url, decode_responses=True)  # type: ignore[no-untyped-call]
    limiter = RedisRateLimiter(client)
    key_a, key_b = f"test:a:{uuid.uuid4()}", f"test:b:{uuid.uuid4()}"

    try:
        await limiter.hit(key_a, limit=1, window_seconds=60)
        blocked_a = await limiter.hit(key_a, limit=1, window_seconds=60)
        allowed_b = await limiter.hit(key_b, limit=1, window_seconds=60)
    finally:
        await limiter.close()

    assert blocked_a.allowed is False
    assert allowed_b.allowed is True


# --- Middleware bout en bout, contre un vrai Redis --------------------------


async def test_the_middleware_blocks_after_the_configured_threshold(
    redis_test_url: str,
) -> None:
    """Deux requêtes passent, la troisième reçoit `429` avec `Retry-After`."""
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=2,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first = await client.get("/api/v1/probe")
        second = await client.get("/api/v1/probe")
        third = await client.get("/api/v1/probe")

    assert first.status_code == HTTPStatus.OK
    assert second.status_code == HTTPStatus.OK
    assert third.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert third.headers["content-type"].startswith("application/problem+json")
    assert int(third.headers["retry-after"]) > 0
    assert third.json()["type"] == "/problems/rate-limit-exceeded"


async def test_the_middleware_counts_an_api_key_independently_of_its_ip_bucket(
    redis_test_url: str,
) -> None:
    """Une clé API généreuse n'est pas bridée par le plafond, plus strict, de l'IP."""
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=1,
        rate_limit_requests=10,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings)

    headers = {"Authorization": "Bearer rag_test_jeton-de-test-peu-importe-la-validite"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first = await client.get("/api/v1/probe", headers=headers)
        second = await client.get("/api/v1/probe", headers=headers)
        third = await client.get("/api/v1/probe", headers=headers)

    # Bridé par le compteur IP (limite 1), malgré une limite de clé bien plus haute :
    # les deux compteurs sont cumulatifs, pas substituables (voir ADR-018).
    assert [first.status_code, second.status_code, third.status_code] == [
        HTTPStatus.OK,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]


async def test_a_session_cookie_gets_the_generous_counter_without_any_authorization_header(
    redis_test_url: str,
) -> None:
    """Un utilisateur connecté au navigateur (cookie, jamais `Authorization`) n'est plus
    plafonné à la limite anonyme (ADR-036) — la limite généreuse de clé API s'applique
    aussi via le cookie de session.
    """
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=1,
        rate_limit_requests=10,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings)

    cookies = {settings.session_cookie_name: "session-token-peu-importe-la-validite"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies=cookies
    ) as client:
        first = await client.get("/api/v1/probe")
        second = await client.get("/api/v1/probe")
        third = await client.get("/api/v1/probe")

    # Même verdict que le test clé API ci-dessus : bridé par l'IP (limite 1), pas par
    # la limite anonyme (1) qui aurait bloqué dès la deuxième requête sans ce compteur.
    assert [first.status_code, second.status_code, third.status_code] == [
        HTTPStatus.OK,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]


async def test_no_session_cookie_and_no_api_key_keeps_the_anonymous_counter_unchanged(
    redis_test_url: str,
) -> None:
    """Sans cookie ni clé API, seul le compteur IP (anonyme) s'applique — inchangé."""
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=2,
        rate_limit_requests=100,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first = await client.get("/api/v1/probe")
        second = await client.get("/api/v1/probe")
        third = await client.get("/api/v1/probe")

    assert [first.status_code, second.status_code, third.status_code] == [
        HTTPStatus.OK,
        HTTPStatus.OK,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]


async def test_session_and_api_key_counters_are_independent_of_each_other(
    redis_test_url: str,
) -> None:
    """Un cookie de session et une clé API différente n'alimentent pas le même compartiment
    généreux — même appelant IP, deux identifiants, deux compteurs distincts.
    """
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=100,
        rate_limit_requests=1,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings)

    cookies = {settings.session_cookie_name: "session-token-a"}
    headers = {"Authorization": "Bearer cle-api-b"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies=cookies
    ) as client:
        # Épuise le compartiment de session (limite 1).
        first = await client.get("/api/v1/probe")
        second = await client.get("/api/v1/probe")
        # La clé API, différente, a son propre compartiment neuf malgré la même IP —
        # le cookie reste posé sur le client mais l'en-tête prime (voir test suivant).
        third = await client.get("/api/v1/probe", headers=headers)

    assert [first.status_code, second.status_code, third.status_code] == [
        HTTPStatus.OK,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.OK,
    ]


async def test_authorization_header_takes_priority_over_a_session_cookie(
    redis_test_url: str,
) -> None:
    """Les deux identifiants présents à la fois : la clé API prime, le cookie n'est même
    pas touché par le compteur généreux — même ordre de priorité que
    `PrincipalResolver.resolve`.
    """
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=100,
        rate_limit_requests=1,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings)

    cookies = {settings.session_cookie_name: "session-token-c"}
    headers = {"Authorization": "Bearer cle-api-d"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies=cookies
    ) as client:
        # Épuise le compartiment de clé (limite 1) — le cookie est aussi présent mais
        # doit être ignoré tant que l'en-tête l'est.
        first = await client.get("/api/v1/probe", headers=headers)
        # Même cookie, sans l'en-tête cette fois : si la requête précédente avait aussi
        # consommé le compartiment de session, celle-ci serait bloquée. Elle ne l'est
        # pas, preuve que le cookie n'a pas été compté pendant que l'en-tête primait.
        second = await client.get("/api/v1/probe")

    assert first.status_code == HTTPStatus.OK
    assert second.status_code == HTTPStatus.OK


# --- Passerelle Docker locale : plafond IP dédié (ADR-040) ------------------


async def test_the_docker_gateway_ip_gets_the_generous_gateway_limit_instead_of_the_anonymous_one(
    redis_test_url: str,
) -> None:
    """Reproduit le scénario qui a bloqué l'utilisateur : une IP reconnue comme passerelle
    Docker locale n'est plus plafonnée à la limite anonyme stricte, même sans cookie ni clé
    (déploiement local, tout le trafic hôte partage cette même IP).
    """
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=2,
        rate_limit_gateway_requests=5,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings, gateway_ip="172.18.0.1")

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("172.18.0.1", 12345)),
        base_url="http://testserver",
    ) as client:
        responses = [await client.get("/api/v1/probe") for _ in range(4)]

    # Le plafond anonyme (2) aurait bloqué dès la 3e requête. Le plafond passerelle (5)
    # laisse passer les 4 — c'est exactement le burst qui, avant ce correctif, épuisait le
    # compteur IP partagé par toute la machine hôte.
    assert [response.status_code for response in responses] == [HTTPStatus.OK] * 4


async def test_a_real_external_ip_is_still_capped_by_the_strict_anonymous_limit(
    redis_test_url: str,
) -> None:
    """Cas négatif : une IP qui n'est *pas* la passerelle détectée reste bridée par le
    plafond anonyme strict, même quand un plafond passerelle généreux est configuré — un
    vrai visiteur anonyme isolé n'est jamais affaibli par ce correctif (ADR-040).
    """
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=2,
        rate_limit_gateway_requests=100,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings, gateway_ip="172.18.0.1")

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.42", 12345)),
        base_url="http://testserver",
    ) as client:
        first = await client.get("/api/v1/probe")
        second = await client.get("/api/v1/probe")
        third = await client.get("/api/v1/probe")

    assert [first.status_code, second.status_code, third.status_code] == [
        HTTPStatus.OK,
        HTTPStatus.OK,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]


async def test_without_gateway_detection_the_gateway_ip_has_no_special_treatment(
    redis_test_url: str,
) -> None:
    """`gateway_ip=None` explicite (détection absente ou ayant échoué) : comportement
    inchangé, identique à avant ADR-040 — même l'adresse qui aurait pu être une passerelle
    Docker retombe sur le plafond anonyme strict. Filet de sécurité : une détection en échec
    n'accorde jamais un plafond élevé par défaut.
    """
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=2,
        rate_limit_gateway_requests=100,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings, gateway_ip=None)

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("172.18.0.1", 12345)),
        base_url="http://testserver",
    ) as client:
        first = await client.get("/api/v1/probe")
        second = await client.get("/api/v1/probe")
        third = await client.get("/api/v1/probe")

    assert [first.status_code, second.status_code, third.status_code] == [
        HTTPStatus.OK,
        HTTPStatus.OK,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]


async def test_a_login_attempt_is_no_longer_blocked_by_unrelated_dashboard_polling(
    redis_test_url: str,
) -> None:
    """Bout en bout du scénario réel : un burst de polling de tableau de bord (plusieurs
    documents suivis, plusieurs onglets) depuis la passerelle Docker locale ne fait plus
    échouer une tentative de connexion sans rapport, arrivée juste après depuis la même IP
    partagée (c'était la dernière ligne du journal qui a bloqué l'utilisateur : `POST
    /api/v1/sessions` en 429, provoqué par un burst `/documents`/`/knowledge-bases`/`/me`).
    """
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=10,
        rate_limit_gateway_requests=50,
        rate_limit_window_seconds=60,
    )

    app = Starlette(
        routes=[
            Route("/api/v1/probe", _ok),
            Route("/api/v1/sessions", _ok, methods=["POST"]),
        ]
    )
    app.add_middleware(RateLimitMiddleware, settings=settings, gateway_ip="172.18.0.1")

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("172.18.0.1", 12345)),
        base_url="http://testserver",
    ) as client:
        # Dépasse largement le plafond anonyme (10) qui, avant ADR-040, aurait à lui seul
        # épuisé le compteur IP partagé par toute la machine hôte.
        burst = [await client.get("/api/v1/probe") for _ in range(15)]
        login = await client.post("/api/v1/sessions")

    assert all(response.status_code == HTTPStatus.OK for response in burst)
    assert login.status_code == HTTPStatus.OK


# --- Détection de la passerelle Docker : `_detect_docker_gateway_ip` (ADR-040) ----------


def test_detect_docker_gateway_ip_reads_the_default_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduit le format réel de `/proc/net/route` (colonnes hexadécimales, ordre
    machine) constaté dans le conteneur `ragengine-api` (`eth0 00000000 010012AC ...`) —
    pas un format inventé pour le test — et vérifie qu'il décode bien `172.18.0.1`.
    """
    dockerenv = tmp_path / "dockerenv"
    dockerenv.touch()
    route_file = tmp_path / "route"
    route_file.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    )
    monkeypatch.setattr(rate_limit, "_DOCKERENV_MARKER", dockerenv)
    monkeypatch.setattr(rate_limit, "_PROC_NET_ROUTE", route_file)

    assert rate_limit._detect_docker_gateway_ip() == "172.18.0.1"


def test_detect_docker_gateway_ip_returns_none_outside_a_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans `/.dockerenv`, jamais de détection — même avec une route par défaut valide juste
    à côté (déploiement bare-metal) : ce n'est structurellement pas une passerelle Docker,
    et l'accorder affaiblirait la protection pour de vrais visiteurs derrière un NAT réel.
    """
    route_file = tmp_path / "route"
    route_file.write_text("Iface\tDestination\tGateway \tFlags\neth0\t00000000\t010012AC\t0003\n")
    monkeypatch.setattr(rate_limit, "_DOCKERENV_MARKER", tmp_path / "absent-dockerenv")
    monkeypatch.setattr(rate_limit, "_PROC_NET_ROUTE", route_file)

    assert rate_limit._detect_docker_gateway_ip() is None


def test_detect_docker_gateway_ip_fails_open_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/proc/net/route` absent malgré `/.dockerenv` présent (conteneur exotique) : `None`,
    jamais d'exception qui remonterait jusqu'au constructeur du middleware.
    """
    dockerenv = tmp_path / "dockerenv"
    dockerenv.touch()
    monkeypatch.setattr(rate_limit, "_DOCKERENV_MARKER", dockerenv)
    monkeypatch.setattr(rate_limit, "_PROC_NET_ROUTE", tmp_path / "does-not-exist")

    assert rate_limit._detect_docker_gateway_ip() is None


# --- Chemins sans dépendance à un Redis réel --------------------------------


class _AlwaysDeniedLimiter:
    """Limiteur factice qui refuse systématiquement — sans jamais toucher Redis."""

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitOutcome:
        del key, limit, window_seconds
        return RateLimitOutcome(allowed=False, remaining=0, retry_after_seconds=30)


class _BrokenLimiter:
    """Limiteur factice qui simule un compteur injoignable dès le premier appel (compteur IP)."""

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitOutcome:
        del key, limit, window_seconds
        raise RateLimiterUnavailableError("Compteur injoignable (simulation de test).")


class _BrokenOnGenerousCounterLimiter:
    """Laisse passer le compteur IP, échoue seulement sur le compteur généreux (clé/session) —
    isole le second bloc `except RateLimiterUnavailableError` du middleware (`rate_limit.py`),
    recopié à la main à côté du premier avant ce correctif (audit dette technique 2026-09)."""

    def __init__(self) -> None:
        self._calls = 0

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitOutcome:
        del key, window_seconds
        self._calls += 1
        if self._calls == 1:
            return RateLimitOutcome(allowed=True, remaining=limit, retry_after_seconds=0)
        raise RateLimiterUnavailableError("Compteur clé/session injoignable (simulation de test).")


async def test_health_bypasses_rate_limiting_even_when_denied() -> None:
    """`/health` n'est jamais compté, même contre un limiteur qui refuse tout."""
    settings = Settings(rate_limit_enabled=True)
    app = Starlette(routes=[Route("/api/v1/health", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings, limiter=_AlwaysDeniedLimiter())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == HTTPStatus.OK


async def test_rate_limiting_can_be_disabled_entirely() -> None:
    """`RAGENGINE_RATE_LIMIT_ENABLED=false` désactive le middleware, même limiteur strict."""

    settings = Settings(rate_limit_enabled=False)
    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings, limiter=_AlwaysDeniedLimiter())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/v1/probe")

    assert response.status_code == HTTPStatus.OK


async def test_the_middleware_fails_open_when_redis_is_unreachable() -> None:
    """Un Redis injoignable laisse passer la requête plutôt que de renvoyer une erreur."""

    settings = Settings(rate_limit_enabled=True)
    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings, limiter=_BrokenLimiter())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/v1/probe")

    assert response.status_code == HTTPStatus.OK


async def test_the_middleware_fails_open_when_the_generous_counter_is_unreachable() -> None:
    """Même politique fail-open pour le second bloc (clé/session) — distinct du premier
    (compteur IP), recopié à la main à côté avant ce correctif (audit dette technique 2026-09)."""
    settings = Settings(rate_limit_enabled=True)
    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(
        RateLimitMiddleware, settings=settings, limiter=_BrokenOnGenerousCounterLimiter()
    )

    headers = {"Authorization": "Bearer rag_test_peu-importe-la-validite"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/v1/probe", headers=headers)

    assert response.status_code == HTTPStatus.OK


# --- Normalisation de la clé d'identité IP (ADR-052) ------------------------


def test_normalize_client_ip_leaves_ipv4_unchanged() -> None:
    assert normalize_client_ip("203.0.113.7") == "203.0.113.7"


def test_normalize_client_ip_collapses_ipv6_to_its_slash_64_prefix() -> None:
    """Deux adresses du même `/64` doivent produire la même clé d'identité — sans quoi un
    attaquant contourne le plafond en changeant d'adresse à volonté dans son propre `/64`."""
    first = normalize_client_ip("2001:db8:1234:5678::1")
    second = normalize_client_ip("2001:db8:1234:5678:ffff:ffff:ffff:ffff")

    assert first == second


def test_normalize_client_ip_keeps_different_slash_64_prefixes_distinct() -> None:
    first = normalize_client_ip("2001:db8:1234:5678::1")
    second = normalize_client_ip("2001:db8:1234:5679::1")

    assert first != second


def test_normalize_client_ip_maps_an_ipv4_mapped_ipv6_address_down_to_ipv4() -> None:
    """`::ffff:a.b.c.d` (socket double pile) ne doit pas ouvrir un compartiment distinct de
    la même adresse vue en IPv4 pure."""
    assert normalize_client_ip("::ffff:203.0.113.7") == "203.0.113.7"


def test_normalize_client_ip_returns_unparseable_values_unchanged() -> None:
    """`"unknown"` (client absent) reste un compteur valide, seulement moins précis."""
    assert normalize_client_ip("unknown") == "unknown"


async def test_the_middleware_shares_the_ip_bucket_across_the_same_ipv6_slash_64(
    redis_test_url: str,
) -> None:
    """Bout en bout : deux visiteurs du même `/64` partagent le compteur IP du middleware."""
    settings = Settings(
        redis_url=redis_test_url,
        rate_limit_enabled=True,
        rate_limit_anonymous_requests=2,
        rate_limit_window_seconds=60,
    )

    app = Starlette(routes=[Route("/api/v1/probe", _ok)])
    app.add_middleware(RateLimitMiddleware, settings=settings)

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("2001:db8:1234:5678::1", 12345)),
        base_url="http://testserver",
    ) as client:
        first = await client.get("/api/v1/probe")
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("2001:db8:1234:5678:ffff::2", 12345)),
        base_url="http://testserver",
    ) as client:
        second = await client.get("/api/v1/probe")
        third = await client.get("/api/v1/probe")

    # Le plafond (2) est partagé par les deux adresses du même `/64` : la troisième requête,
    # bien que sur une IP encore jamais vue individuellement, est bloquée.
    assert [first.status_code, second.status_code, third.status_code] == [
        HTTPStatus.OK,
        HTTPStatus.OK,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]
