"""En-têtes de sécurité : middleware de l'API et middleware Traefik (ADR-052).

Deux émetteurs pour les mêmes en-têtes : Traefik (`docker/traefik/dynamic.yml`,
routeurs `api` et `frontend`) écrase ceux de l'API. S'ils divergeaient, une même
réponse changerait de politique selon qu'elle passe par le proxy ou par le port
publié — ces tests lisent le vrai fichier Traefik et le comparent au module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from httpx import ASGITransport, AsyncClient, Response
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.config import Settings
from app.core.security_headers import (
    HSTS_MAX_AGE_SECONDS,
    STATIC_SECURITY_HEADERS,
    SecurityHeadersMiddleware,
)

_DYNAMIC_YML = Path(__file__).resolve().parents[2] / "docker" / "traefik" / "dynamic.yml"


def _build_app() -> Starlette:
    async def plain(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def framed(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok", headers={"X-Frame-Options": "SAMEORIGIN"})

    app = Starlette(routes=[Route("/plain", plain), Route("/framed", framed)])
    app.add_middleware(SecurityHeadersMiddleware)
    return app


async def _get(base_url: str, path: str, headers: dict[str, str] | None = None) -> Response:
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(transport=transport, base_url=base_url) as client:
        return await client.get(path, headers=headers)


def _traefik_config() -> dict[str, Any]:
    with _DYNAMIC_YML.open(encoding="utf-8") as handle:
        config: dict[str, Any] = yaml.safe_load(handle)
    return config["http"]


async def test_every_static_header_is_set_once_on_an_api_response() -> None:
    response = await _get("http://test", "/plain")

    for name, value in STATIC_SECURITY_HEADERS.items():
        assert response.headers.get_list(name) == [value]


async def test_a_header_chosen_by_the_route_is_not_overwritten() -> None:
    response = await _get("http://test", "/framed")

    assert response.headers["x-frame-options"] == "SAMEORIGIN"


async def test_hsts_is_sent_over_https_without_include_subdomains() -> None:
    response = await _get("https://test", "/plain")

    assert response.headers["strict-transport-security"] == f"max-age={HSTS_MAX_AGE_SECONDS}"


async def test_a_forged_forwarded_proto_does_not_turn_hsts_on() -> None:
    # Le schéma HTTPS vient d'uvicorn, qui ne reporte `X-Forwarded-Proto` que
    # venant d'un proxy approuvé ; l'en-tête brut n'est jamais relu ici.
    response = await _get("http://test", "/plain", headers={"X-Forwarded-Proto": "https"})

    assert "strict-transport-security" not in response.headers


def test_traefik_sets_the_same_headers_as_the_api() -> None:
    headers = _traefik_config()["middlewares"]["security-headers"]["headers"]

    emitted = {
        "X-Content-Type-Options": "nosniff" if headers.get("contentTypeNosniff") else None,
        "X-Frame-Options": "DENY" if headers.get("frameDeny") else None,
        "Content-Security-Policy": headers.get("contentSecurityPolicy"),
        "Referrer-Policy": headers.get("referrerPolicy"),
        **headers.get("customResponseHeaders", {}),
    }
    assert emitted == STATIC_SECURITY_HEADERS
    assert headers["stsSeconds"] == HSTS_MAX_AGE_SECONDS
    assert headers["forceSTSHeader"] is True
    assert not headers.get("stsIncludeSubdomains")
    assert not headers.get("stsPreload")


def test_every_traefik_router_carries_the_security_headers() -> None:
    routers = _traefik_config()["routers"]

    assert {"api", "frontend"} <= routers.keys()
    for name, router in routers.items():
        assert "security-headers" in router.get("middlewares", []), name


def test_every_api_router_bounds_the_request_body_at_the_proxy() -> None:
    http = _traefik_config()
    routers, middlewares = http["routers"], http["middlewares"]
    api_limit = middlewares["body-limit-api"]["buffering"]["maxRequestBodyBytes"]

    for name, router in routers.items():
        if "/api" not in router["rule"]:
            continue
        limits = [m for m in router["middlewares"] if "buffering" in middlewares.get(m, {})]
        if name == "api-chat-stream":
            # Réponse SSE : jamais de `buffering` (il met la réponse en tampon),
            # la borne est portée par la règle — au plus 6 chiffres.
            assert limits == []
            assert "HeaderRegexp(`Content-Length`, `^[0-9]{1,6}$`)" in router["rule"]
            assert api_limit >= 999_999
        else:
            assert len(limits) == 1, name


def test_the_upload_route_admits_the_application_ceiling_plus_multipart() -> None:
    upload = _traefik_config()["middlewares"]["body-limit-upload"]["buffering"]
    application_ceiling = Settings.model_fields["upload_max_bytes"].default

    assert upload["maxRequestBodyBytes"] > application_ceiling
    assert upload["maxRequestBodyBytes"] - application_ceiling <= 2 * 1024 * 1024
