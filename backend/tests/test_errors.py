"""Tests du contrat d'erreur RFC 9457 (`application/problem+json`)."""

from collections.abc import AsyncIterator
from http import HTTPStatus

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient, Response

from app.core.errors import (
    PROBLEM_MEDIA_TYPE,
    ConflictError,
    NotFoundError,
    TooManyRequestsError,
)
from tests.conftest import BASE_URL

_PREFIX = "/api/v1/_tests"


@pytest.fixture
def error_app(app: FastAPI) -> FastAPI:
    """Application enrichie de routes qui échouent, dédiées à ces tests."""

    @app.get(f"{_PREFIX}/not-found")
    async def _not_found() -> None:
        raise NotFoundError(
            "La base de connaissances 'kb_42' est introuvable.",
            extensions={"resource_id": "kb_42"},
        )

    @app.get(f"{_PREFIX}/conflict")
    async def _conflict() -> None:
        raise ConflictError("Le job est déjà en cours d'indexation.")

    @app.get(f"{_PREFIX}/quota-exhausted")
    async def _quota_exhausted() -> None:
        raise TooManyRequestsError("Plafond atteint.", extensions={"retry_after_seconds": 1800})

    @app.get(f"{_PREFIX}/quota-exhausted-without-delay")
    async def _quota_exhausted_without_delay() -> None:
        raise TooManyRequestsError("Plafond atteint.")

    @app.get(f"{_PREFIX}/teapot")
    async def _teapot() -> None:
        raise HTTPException(status_code=HTTPStatus.IM_A_TEAPOT, detail="Théière hors service.")

    @app.get(f"{_PREFIX}/non-standard-status")
    async def _non_standard_status() -> None:
        raise HTTPException(status_code=599, detail="Code hors registre HTTP.")

    @app.get(f"{_PREFIX}/items/{{item_id}}")
    async def _item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get(f"{_PREFIX}/boom")
    async def _boom() -> None:
        raise RuntimeError("panne interne non anticipée")

    return app


@pytest.fixture
async def error_client(error_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client qui laisse les erreurs serveur produire une réponse au lieu de remonter."""
    transport = ASGITransport(app=error_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url=BASE_URL) as http_client:
        yield http_client


def assert_problem(response: Response, *, status: int, instance: str) -> dict[str, object]:
    """Vérifie l'enveloppe commune RFC 9457 et renvoie le document."""
    assert response.status_code == status
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)

    problem: dict[str, object] = response.json()
    assert problem["status"] == status
    assert isinstance(problem["type"], str)
    assert isinstance(problem["title"], str)
    assert problem["instance"] == instance
    return problem


async def test_domain_error_is_rendered_as_problem_detail(error_client: AsyncClient) -> None:
    """Une erreur métier porte son type, son détail et ses extensions."""
    path = f"{_PREFIX}/not-found"
    problem = assert_problem(
        await error_client.get(path), status=HTTPStatus.NOT_FOUND, instance=path
    )

    assert problem["type"] == "/problems/not-found"
    assert problem["title"] == "Not Found"
    assert problem["detail"] == "La base de connaissances 'kb_42' est introuvable."
    assert problem["resource_id"] == "kb_42"


async def test_conflict_error_uses_409(error_client: AsyncClient) -> None:
    """Les conflits d'état remontent en 409, comme l'impose le contrat d'API."""
    path = f"{_PREFIX}/conflict"
    problem = assert_problem(
        await error_client.get(path), status=HTTPStatus.CONFLICT, instance=path
    )

    assert problem["type"] == "/problems/conflict"


async def test_request_validation_error_is_rendered_as_problem_detail(
    error_client: AsyncClient,
) -> None:
    """Une entrée invalide produit un 422 documenté par l'extension `errors`."""
    path = f"{_PREFIX}/items/not-an-int"
    problem = assert_problem(
        await error_client.get(path), status=HTTPStatus.UNPROCESSABLE_ENTITY, instance=path
    )

    assert problem["type"] == "/problems/validation-error"
    errors = problem["errors"]
    assert isinstance(errors, list)
    assert errors, "l'extension `errors` doit détailler les champs fautifs"


async def test_http_exception_is_rendered_as_problem_detail(error_client: AsyncClient) -> None:
    """Les `HTTPException` levées par le framework passent par la même enveloppe."""
    path = f"{_PREFIX}/teapot"
    problem = assert_problem(
        await error_client.get(path), status=HTTPStatus.IM_A_TEAPOT, instance=path
    )

    assert problem["title"] == "I'm a Teapot"
    assert problem["detail"] == "Théière hors service."


async def test_non_standard_status_falls_back_to_a_generic_title(
    error_client: AsyncClient,
) -> None:
    """Un code hors registre HTTP conserve une enveloppe valide."""
    path = f"{_PREFIX}/non-standard-status"
    problem = assert_problem(await error_client.get(path), status=599, instance=path)

    assert problem["title"] == "Error"
    assert problem["detail"] == "Code hors registre HTTP."


async def test_unhandled_error_is_rendered_as_problem_detail(error_client: AsyncClient) -> None:
    """Une exception non prévue ne fuit ni trace ni message interne."""
    path = f"{_PREFIX}/boom"
    problem = assert_problem(
        await error_client.get(path), status=HTTPStatus.INTERNAL_SERVER_ERROR, instance=path
    )

    assert problem["detail"] == "Une erreur interne est survenue."
    assert "panne interne" not in str(problem)


async def test_unknown_route_is_rendered_as_problem_detail(client: AsyncClient) -> None:
    """Le 404 de routage lui-même respecte le contrat d'erreur."""
    path = "/api/v1/inexistant"
    assert_problem(await client.get(path), status=HTTPStatus.NOT_FOUND, instance=path)


async def test_a_raised_429_carries_the_retry_after_header(error_client: AsyncClient) -> None:
    """ADR-054 : un `429` levé par un service (quota LLM, créations d'invités) porte le même
    en-tête `Retry-After` que celui du middleware de rate limiting."""
    response = await error_client.get(f"{_PREFIX}/quota-exhausted")

    problem = assert_problem(
        response, status=HTTPStatus.TOO_MANY_REQUESTS, instance=f"{_PREFIX}/quota-exhausted"
    )
    assert response.headers["retry-after"] == "1800"
    assert problem["retry_after_seconds"] == 1800


async def test_a_raised_429_without_a_delay_has_no_retry_after_header(
    error_client: AsyncClient,
) -> None:
    response = await error_client.get(f"{_PREFIX}/quota-exhausted-without-delay")

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert "retry-after" not in response.headers
