"""Compteur d'octets applicatif sur le corps des requêtes (audit sécurité 2026-09,
constat 13).

Application Starlette jetable, comme `tests/test_security_headers.py` — pas besoin de
la façade FastAPI complète pour éprouver un middleware ASGI en isolation. Les corps
« sans `Content-Length` » sont simulés en passant un itérateur asynchrone à `httpx`
(`ASGITransport` les relaie alors en plusieurs messages `http.request`, comme un vrai
flux `chunked`, sans jamais annoncer la taille totale à l'avance) — c'est le cas que
le en-tête seul ne peut pas intercepter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import yaml
from httpx import ASGITransport, AsyncClient, Response
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app.core.body_limit import (
    UPLOAD_MULTIPART_ENVELOPE_BYTES,
    RequestBodyLimitMiddleware,
)
from app.core.config import Settings
from app.core.errors import PROBLEM_MEDIA_TYPE

INGESTION_PATH = "/api/v1/knowledge-bases/11111111-1111-1111-1111-111111111111/documents/ingest"


async def _echo_length(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"received": len(body)})


async def _stream_events(request: Request) -> StreamingResponse:
    async def events() -> AsyncIterator[bytes]:
        for i in range(5):
            yield f"data: {i}\n\n".encode()

    return StreamingResponse(events(), media_type="text/event-stream")


def _build_app(settings: Settings) -> Starlette:
    app = Starlette(
        routes=[
            Route("/api/v1/retrieval/chat", _echo_length, methods=["POST"]),
            Route(INGESTION_PATH, _echo_length, methods=["POST"]),
            Route("/api/v1/retrieval/chat-stream", _stream_events, methods=["POST"]),
        ]
    )
    app.add_middleware(RequestBodyLimitMiddleware, settings=settings)
    return app


async def _post(
    settings: Settings, path: str, *, content: bytes | AsyncIterator[bytes]
) -> Response:
    transport = ASGITransport(app=_build_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, content=content)


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    """Générateur asynchrone : `httpx` l'envoie sans `Content-Length` (transfert
    par morceaux), exactement le cas qu'un contrôle sur le seul en-tête manquerait."""
    for part in parts:
        yield part


async def test_a_small_body_is_admitted_unchanged() -> None:
    settings = Settings(RAGENGINE_REQUEST_BODY_MAX_BYTES=1_000)
    response = await _post(settings, "/api/v1/retrieval/chat", content=b"petit corps")

    assert response.status_code == 200
    assert response.json() == {"received": len("petit corps")}


async def test_a_body_over_the_default_limit_is_refused_with_problem_details() -> None:
    settings = Settings(RAGENGINE_REQUEST_BODY_MAX_BYTES=10)
    response = await _post(settings, "/api/v1/retrieval/chat", content=b"beaucoup plus que dix")

    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    body = response.json()
    assert body["status"] == 413
    assert body["type"] == "/problems/payload-too-large"
    assert body["limit_bytes"] == 10


async def test_a_declared_content_length_over_the_limit_is_refused_before_reading_a_byte() -> None:
    """Rejet rapide : `Content-Length` annonce déjà le dépassement."""
    settings = Settings(RAGENGINE_REQUEST_BODY_MAX_BYTES=5)

    response = await _post(settings, "/api/v1/retrieval/chat", content=b"012345678")

    assert response.status_code == 413


async def test_a_chunked_body_without_content_length_is_still_counted_and_refused() -> None:
    """Pas de `Content-Length` (flux `chunked`) : seul le compteur réel protège ici."""
    settings = Settings(RAGENGINE_REQUEST_BODY_MAX_BYTES=10)

    response = await _post(
        settings, "/api/v1/retrieval/chat", content=_chunks(b"0123456789", b"encore un peu")
    )

    assert response.status_code == 413
    assert response.json()["limit_bytes"] == 10


async def test_a_chunked_body_within_the_limit_is_admitted_and_reassembled() -> None:
    """Le rejeu tamponné restitue le corps intact à l'application interne."""
    settings = Settings(RAGENGINE_REQUEST_BODY_MAX_BYTES=1_000)

    response = await _post(
        settings, "/api/v1/retrieval/chat", content=_chunks(b"premiere-partie-", b"seconde-partie")
    )

    assert response.status_code == 200
    assert response.json() == {"received": len("premiere-partie-seconde-partie")}


async def test_the_ingestion_route_gets_the_upload_ceiling_not_the_low_default() -> None:
    """Compatible avec `RAGENGINE_UPLOAD_MAX_BYTES` : un corps qui dépasserait le
    plafond bas générique passe sur la route d'ingestion, sous le plafond d'upload."""
    settings = Settings(RAGENGINE_REQUEST_BODY_MAX_BYTES=10, RAGENGINE_UPLOAD_MAX_BYTES=1_000)
    body = b"x" * 200  # > 10 (plafond bas) mais < 1000 + enveloppe (plafond d'upload)

    low_ceiling = await _post(settings, "/api/v1/retrieval/chat", content=body)
    ingestion = await _post(settings, INGESTION_PATH, content=body)

    assert low_ceiling.status_code == 413
    assert ingestion.status_code == 200
    assert ingestion.json() == {"received": 200}


async def test_the_ingestion_route_still_refuses_beyond_the_upload_ceiling_plus_envelope() -> None:
    settings = Settings(RAGENGINE_UPLOAD_MAX_BYTES=1_000)
    body = b"x" * (1_000 + UPLOAD_MULTIPART_ENVELOPE_BYTES + 1)

    response = await _post(settings, INGESTION_PATH, content=body)

    assert response.status_code == 413
    assert response.json()["limit_bytes"] == 1_000 + UPLOAD_MULTIPART_ENVELOPE_BYTES


async def test_a_get_request_without_a_body_passes_through() -> None:
    settings = Settings(RAGENGINE_REQUEST_BODY_MAX_BYTES=1)
    transport = ASGITransport(app=_build_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/retrieval/chat", content=b"")

    assert response.status_code == 200
    assert response.json() == {"received": 0}


async def test_a_streaming_response_is_never_touched_by_the_body_limit() -> None:
    """Le flux SSE du chat est une préoccupation de réponse (`send`), jamais de requête
    (`receive`) : ce middleware ne le voit pas, quel que soit le plafond appliqué."""
    settings = Settings(RAGENGINE_REQUEST_BODY_MAX_BYTES=1)
    transport = ASGITransport(app=_build_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/retrieval/chat-stream", content=b"")

    assert response.status_code == 200
    assert response.text == "".join(f"data: {i}\n\n" for i in range(5))


_DYNAMIC_YML = Path(__file__).resolve().parents[2] / "docker" / "traefik" / "dynamic.yml"


def _traefik_upload_body_limit() -> int:
    with _DYNAMIC_YML.open(encoding="utf-8") as handle:
        config: dict[str, Any] = yaml.safe_load(handle)
    limit: int = config["http"]["middlewares"]["body-limit-upload"]["buffering"][
        "maxRequestBodyBytes"
    ]
    return limit


def test_the_default_upload_ceiling_stays_under_the_cloudflare_free_plan_limit() -> None:
    """ADR-066 : le plan Cloudflare gratuit coupe un corps de requête à ~100 000 000
    octets décimaux — le plafond applicatif, enveloppe multipart comprise, doit rester
    en dessous, pour que le refus reste le nôtre (`413` lisible) plutôt que la page
    d'erreur brute de Cloudflare."""
    settings = Settings()

    assert settings.upload_max_bytes + UPLOAD_MULTIPART_ENVELOPE_BYTES < 100_000_000


def test_traefik_s_upload_body_limit_matches_the_application_default() -> None:
    """`docker/traefik/dynamic.yml` (ADR-052) et le défaut applicatif (ADR-066) doivent
    rester le même nombre — relever l'un sans l'autre romprait soit l'upload (Traefik
    coupe avant l'application), soit la marge sous le plafond Cloudflare."""
    settings = Settings()

    assert (
        _traefik_upload_body_limit() == settings.upload_max_bytes + UPLOAD_MULTIPART_ENVELOPE_BYTES
    )
