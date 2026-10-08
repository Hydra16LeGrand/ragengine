"""Parcours essentiel du cœur seul, par l'API HTTP (ADR-069).

Application construite sans aucun greffon : premier compte, connexion, création d'une
base, dépôt d'un document, recherche, chat. Le moteur d'ingestion et le LLM sont des
doubles en mémoire (aucun appel réseau) ; tout le reste — routeurs, services, base —
est le code réel.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_storage
from app.db.models import KnowledgeBase
from app.main import API_PREFIX
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.llm.base import ChatMessage, LLMCapabilities
from app.providers.storage import LocalFilesystemStorageAdapter, StoragePort
from app.services import retrieval as retrieval_service
from tests.api.v1.test_knowledge_base_routes import file_bytes

CREDENTIALS = {"email": "premier@example.com", "password": "mot-de-passe-solide-2026"}


@dataclass(slots=True)
class ScriptedLLM:
    fragments: Sequence[str] = ("Bonjour", " le monde")
    capabilities: LLMCapabilities = field(
        default_factory=lambda: LLMCapabilities(
            provider_id="fake-chat-llm",
            vendor="anthropic",
            context_window=200_000,
            supports_streaming=True,
        )
    )

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        raise NotImplementedError

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        for fragment in self.fragments:
            yield fragment


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> FakeIngestionEngine:
    fake = FakeIngestionEngine(strict=False)
    monkeypatch.setattr(retrieval_service, "resolve_ingestion_engine", lambda settings: fake)
    monkeypatch.setattr(
        retrieval_service,
        "resolve_llm_provider",
        lambda provider_id, settings, **_kw: ScriptedLLM(),
    )
    return fake


async def test_the_essential_journey_runs_on_the_core_alone(
    core_app: FastAPI,
    core_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
    fake_engine: FakeIngestionEngine,
) -> None:
    storage = LocalFilesystemStorageAdapter(root=tmp_path / "documents")

    def _storage() -> StoragePort:
        return storage

    core_app.dependency_overrides[get_storage] = _storage

    # Premier compte, puis connexion.
    registered = await core_client.post(
        f"{API_PREFIX}/users", json={**CREDENTIALS, "display_name": "Premier"}
    )
    assert registered.status_code == HTTPStatus.CREATED
    logged_in = await core_client.post(f"{API_PREFIX}/sessions", json=CREDENTIALS)
    assert logged_in.status_code == HTTPStatus.CREATED
    me = await core_client.get(f"{API_PREFIX}/me")
    assert me.status_code == HTTPStatus.OK

    # Création d'une base, dépôt d'un document (job asynchrone accepté).
    created = await core_client.post(
        f"{API_PREFIX}/knowledge-bases", json={"name": "Contrats", "embedding_model_id": "bge-m3"}
    )
    assert created.status_code == HTTPStatus.CREATED
    base_id = created.json()["id"]
    ingested = await core_client.post(
        f"{API_PREFIX}/knowledge-bases/{base_id}/documents/ingest",
        files={"file": ("contrat.pdf", file_bytes("contrat.pdf"), "application/pdf")},
    )
    assert ingested.status_code == HTTPStatus.ACCEPTED
    job = await core_client.get(f"{API_PREFIX}/jobs/{ingested.json()['current_job_id']}")
    assert job.status_code == HTTPStatus.OK
    assert job.json()["state"] == "queued"

    # Le moteur n'est pas joint : la base est marquée « ingérée » pour atteindre la recherche.
    knowledge_base = (await db_session.scalars(select(KnowledgeBase))).one()
    knowledge_base.ragflow_dataset_external_id = "dataset-externe"
    await db_session.flush()

    # Recherche, puis chat (flux SSE sources -> tokens -> done).
    search = await core_client.post(
        f"{API_PREFIX}/retrieval/search", json={"knowledge_base_id": base_id, "query": "durée"}
    )
    assert search.status_code == HTTPStatus.OK, search.text
    chat = await core_client.post(
        f"{API_PREFIX}/retrieval/chat",
        json={
            "knowledge_base_id": base_id,
            "messages": [{"role": "user", "content": "question"}],
            "llm_provider_id": "claude-opus-5",
        },
    )
    assert chat.status_code == HTTPStatus.OK, chat.text
    names = [block.split("\n", 1)[0] for block in chat.text.strip("\n").split("\n\n")]
    assert names == ["event: sources", "event: token", "event: token", "event: done"]
    tokens = [
        json.loads(block.split("data: ", 1)[1])
        for block in chat.text.strip("\n").split("\n\n")
        if block.startswith("event: token")
    ]
    assert tokens == ["Bonjour", " le monde"]
    assert len(fake_engine.search_calls) == 2


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/chatbots/un-lien"),
        ("POST", "/chatbots/un-lien/chat"),
        ("POST", "/guest-sessions"),
        ("POST", "/guest-sessions/elevate"),
        ("GET", "/admin/users"),
        ("GET", "/admin/usage"),
    ],
)
async def test_no_route_of_the_platform_is_registered(
    core_client: AsyncClient, method: str, path: str
) -> None:
    """Un appel anonyme répondrait 401 si la route existait ; ici, rien ne l'écoute."""
    response = await core_client.request(method, f"{API_PREFIX}{path}")

    assert response.status_code in (HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED)
