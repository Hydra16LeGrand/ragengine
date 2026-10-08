"""Référence développeur publique (`app/api/public_docs.py`).

Deux garanties à défendre : le schéma filtré n'expose que les tags de
`PUBLIC_DOC_TAGS` (aucune fuite d'`organizations`, ni dans les chemins ni
dans les schémas de composants), et les routes publiques répondent dans
tous les environnements — contrairement au Swagger/Redoc complets, gatés au
développement seul (`app/main.py`).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.public_docs import PUBLIC_DOC_TAGS, build_public_openapi_schema


def test_filtered_schema_excludes_organizations_and_unlisted_tags(app: FastAPI) -> None:
    schema = build_public_openapi_schema(app)

    found_tags: set[str] = set()
    for operations in schema["paths"].values():
        for operation in operations.values():
            found_tags.update(operation.get("tags", []))

    assert found_tags <= PUBLIC_DOC_TAGS
    assert "organizations" not in found_tags
    assert not any("organization" in path.lower() for path in schema["paths"])
    assert not any("organization" in name.lower() for name in schema["components"]["schemas"])


async def test_public_docs_routes_respond_in_development(client: AsyncClient) -> None:
    response = await client.get("/api/v1/openapi-public.json")
    assert response.status_code == 200
    assert response.json()["paths"]

    page = await client.get("/api/v1/docs-public")
    assert page.status_code == 200
    assert "<redoc" in page.text
    assert "/api/v1/openapi-public.json" in page.text


async def test_public_docs_routes_respond_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contrairement à `/api/v1/docs` (dev seul), la référence filtrée reste servie."""
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("RAGENGINE_ENV", "staging")
    monkeypatch.setenv("RAGENGINE_SECRET_KEY", "a-non-default-test-only-secret-key")
    monkeypatch.setenv("POSTGRES_PASSWORD", "a-non-default-test-only-postgres-password")
    monkeypatch.setenv("RAGFLOW_BOOTSTRAP_PASSWORD", "a-non-default-test-only-ragflow-password")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6380/0")
    monkeypatch.setenv("RAGENGINE_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    staging_app = create_app()

    from httpx import ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=staging_app), base_url="http://test"
    ) as staging_client:
        full_docs = await staging_client.get("/api/v1/docs")
        assert full_docs.status_code == 404

        public_json = await staging_client.get("/api/v1/openapi-public.json")
        assert public_json.status_code == 200

        public_page = await staging_client.get("/api/v1/docs-public")
        assert public_page.status_code == 200
