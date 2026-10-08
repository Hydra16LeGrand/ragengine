"""`GET/PUT/DELETE /api/v1/me/llm-keys*` — portées de clé API (ADR-063).

Distinct du reste de `/me*` (`GET/PATCH /me`, non scopé, ADR-018) : une clé LLM
tierce déclarée ici est un secret de facturation externe, pas un réglage de
profil — audit sécurité 2026-09, constat 13.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes import llm_keys
from app.db.models import User
from app.domain.schemas.api_key import ApiKeyCreate
from app.domain.schemas.user import UserCreate
from app.domain.scopes import ApiKeyScope
from app.main import API_PREFIX
from app.services.identity.api_keys import ApiKeyService
from app.services.identity.users import UserService
from tests.conftest import BASE_URL
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


@pytest.fixture
def llm_keys_app(identity_app: FastAPI) -> FastAPI:
    """`identity_app` (session de test, sans commit) augmentée du routeur `/me/llm-keys`,
    lui non plus non enregistré hors de la fenêtre principale (même raison que `crud_app`,
    `tests/api/v1/conftest.py`)."""
    identity_app.include_router(llm_keys.router, prefix=API_PREFIX)
    return identity_app


@pytest.fixture
def client(llm_keys_app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=llm_keys_app), base_url=BASE_URL)


async def _create_account(db: AsyncSession) -> User:
    return await UserService(db).register(
        UserCreate(
            email=factories.unique_email("llmkey-route"),
            password=SecretStr(PASSWORD),
            display_name="Amara",
        )
    )


async def _issue_api_key_for(db: AsyncSession, user: User, *, scopes: list[str]) -> str:
    issued = await ApiKeyService(db).create(user, ApiKeyCreate(name="Clé de test", scopes=scopes))
    return issued.token


async def test_a_key_without_the_llm_keys_scope_cannot_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Une clé scopée à un périmètre métier étroit (`documents:read`) n'ouvre plus
    `/me/llm-keys` : avant ce correctif, cette route n'était protégée par aucun scope."""
    user = await _create_account(db_session)
    token = await _issue_api_key_for(db_session, user, scopes=[ApiKeyScope.DOCUMENTS_READ.value])

    response = await client.get("/api/v1/me/llm-keys", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json()["required_scope"] == ApiKeyScope.LLM_KEYS_READ.value


async def test_a_key_with_the_read_scope_can_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_account(db_session)
    token = await _issue_api_key_for(db_session, user, scopes=[ApiKeyScope.LLM_KEYS_READ.value])

    response = await client.get("/api/v1/me/llm-keys", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


async def test_a_read_scope_does_not_admit_declaring_a_key(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Pas de hiérarchie implicite (ADR-018) : `LLM_KEYS_READ` ne couvre pas `PUT`."""
    user = await _create_account(db_session)
    token = await _issue_api_key_for(db_session, user, scopes=[ApiKeyScope.LLM_KEYS_READ.value])

    response = await client.put(
        "/api/v1/me/llm-keys/anthropic",
        json={"api_key": "sk-ant-perso-123"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["required_scope"] == ApiKeyScope.LLM_KEYS_WRITE.value


async def test_a_key_with_the_write_scope_can_declare_and_remove(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_account(db_session)
    token = await _issue_api_key_for(db_session, user, scopes=[ApiKeyScope.LLM_KEYS_WRITE.value])
    headers = {"Authorization": f"Bearer {token}"}

    declared = await client.put(
        "/api/v1/me/llm-keys/anthropic", json={"api_key": "sk-ant-perso-123"}, headers=headers
    )
    assert declared.status_code == 200

    removed = await client.delete("/api/v1/me/llm-keys/anthropic", headers=headers)
    assert removed.status_code == 204


async def test_the_wildcard_scope_still_admits_everything(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Rétrocompatibilité des clés pré-Phase 9 (ADR-018) : `"*"` couvre aussi ces deux
    portées neuves, sans qu'aucune clé existante ne perde d'accès à leur ajout."""
    user = await _create_account(db_session)
    token = await _issue_api_key_for(db_session, user, scopes=["*"])

    response = await client.get("/api/v1/me/llm-keys", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
