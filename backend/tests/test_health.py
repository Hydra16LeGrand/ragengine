"""Tests de l'endpoint de supervision `GET /api/v1/health`."""

from http import HTTPStatus

from fastapi import FastAPI
from httpx import AsyncClient

from app import __version__
from app.core.config import Environment, get_settings


async def test_health_returns_ok(client: AsyncClient) -> None:
    """La sonde répond 200 avec le contrat typé attendu."""
    response = await client.get("/api/v1/health")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "environment": Environment.DEVELOPMENT.value,
    }


async def test_health_is_declared_in_openapi(app: FastAPI) -> None:
    """L'endpoint est publié dans le schéma OpenAPI servi aux clients."""
    schema = app.openapi()

    assert schema["info"]["title"] == f"{get_settings().app_name} API"
    assert schema["info"]["version"] == "v1"
    assert "/api/v1/health" in schema["paths"]
