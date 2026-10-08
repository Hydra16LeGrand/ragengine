"""Parcours complet sans RAGFlow ni GPU : `INGESTION_ENGINE=fake` (ADR-070).

Fondation du futur chemin d'essai léger : une base, un document déposé par
l'API, le pipeline Celery mené jusqu'à `completed` (tâches exécutées en mémoire,
sur une base qui commite pour de vrai), puis une recherche qui retrouve le
texte déposé — et le catalogue qui annonce le moteur comme factice.

Le moteur factice vit dans le process : l'API (cette boucle) et les tâches
(un thread chacune) en partagent l'unique instance, ce que la fabrique garantit.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.dependencies import get_storage
from app.core.config import get_settings
from app.db.session import get_db_session
from app.main import API_PREFIX
from app.providers.engine.fake import reset_shared_fake_engine
from app.providers.storage import LocalFilesystemStorageAdapter, StoragePort
from app.workers import session as worker_session
from app.workers.celery_app import app as celery_app
from app.workers.tasks import ingestion
from app.workers.tasks.ingestion import run_chunking, run_precheck
from tests.conftest import BASE_URL
from tests.postgres import (
    drop_database,
    recreate_database,
    resolve_test_database_url,
    run_upgrade,
)

PASSWORD = "mot-de-passe-solide-2026"
TEXT = (
    "Le délai de remboursement est de trente jours.\n\n"
    "La garantie couvre les défauts de fabrication.\n\n"
    "Résiliation : préavis de deux mois."
)


@pytest.fixture(scope="module")
def e2e_database(database_url: URL) -> Iterator[URL]:
    """Base dédiée au module : les tâches y commitent pour de vrai."""
    url = resolve_test_database_url(f"{database_url.database}_fake_engine")
    recreate_database(url)
    run_upgrade(url)
    try:
        yield url
    finally:
        drop_database(url)


@pytest.fixture
def fake_engine_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("INGESTION_ENGINE", "fake")
    get_settings.cache_clear()
    reset_shared_fake_engine()
    yield
    reset_shared_fake_engine()
    get_settings.cache_clear()


@pytest.fixture
async def e2e_client(
    fake_engine_environment: None,
    app: FastAPI,
    e2e_database: URL,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(e2e_database, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)
    storage = LocalFilesystemStorageAdapter(root=tmp_path / "documents")

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()

    def _storage() -> StoragePort:
        return storage

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_storage] = _storage
    # Les tâches Celery : même base, même stockage, exécution en mémoire.
    monkeypatch.setattr(worker_session, "get_session_factory", lambda: factory)
    monkeypatch.setattr(ingestion, "_storage", lambda: storage)
    conf = celery_app.conf
    previous = (conf.task_always_eager, conf.task_eager_propagates)
    conf.task_always_eager = True
    conf.task_eager_propagates = True
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            yield client
    finally:
        conf.task_always_eager, conf.task_eager_propagates = previous
        await engine.dispose()


async def test_the_whole_api_runs_on_the_fake_engine(e2e_client: AsyncClient) -> None:
    client = e2e_client

    catalog = (await client.get(f"{API_PREFIX}/providers")).json()
    assert catalog["engine"]["provider_id"] == "fake"
    assert catalog["engine"]["is_fake"] is True

    email = f"fake-{uuid.uuid4().hex[:10]}@example.com"
    created = await client.post(
        f"{API_PREFIX}/users", json={"email": email, "password": PASSWORD, "display_name": "Essai"}
    )
    assert created.status_code == HTTPStatus.CREATED, created.text
    logged = await client.post(
        f"{API_PREFIX}/sessions", json={"email": email, "password": PASSWORD}
    )
    assert logged.status_code == HTTPStatus.CREATED, logged.text
    base = await client.post(
        f"{API_PREFIX}/knowledge-bases", json={"name": "Essai", "embedding_model_id": "bge-m3"}
    )
    assert base.status_code == HTTPStatus.CREATED, base.text
    base_id = base.json()["id"]

    ingested = await client.post(
        f"{API_PREFIX}/knowledge-bases/{base_id}/documents/ingest",
        files={"file": ("contrat.txt", TEXT.encode(), "text/plain")},
    )
    assert ingested.status_code == HTTPStatus.ACCEPTED, ingested.text
    document_id, job_id = ingested.json()["id"], ingested.json()["current_job_id"]

    # Precheck (queue gpu en production), puis la validation humaine, puis le reste du pipeline.
    await asyncio.to_thread(run_precheck, job_id)
    job = (await client.get(f"{API_PREFIX}/jobs/{job_id}")).json()
    assert job["state"] == "awaiting_review"
    decided = await client.put(
        f"{API_PREFIX}/documents/{document_id}/precheck/decision", json={"decision": {}}
    )
    assert decided.status_code == HTTPStatus.OK, decided.text
    await asyncio.to_thread(run_chunking, job_id)

    job = (await client.get(f"{API_PREFIX}/jobs/{job_id}")).json()
    assert job["state"] == "completed", job

    found = await client.post(
        f"{API_PREFIX}/retrieval/search",
        json={"knowledge_base_id": base_id, "query": "délai de remboursement"},
    )
    assert found.status_code == HTTPStatus.OK, found.text
    contents = [result["content"] for result in found.json()["results"]]
    assert contents[0] == "Le délai de remboursement est de trente jours."
