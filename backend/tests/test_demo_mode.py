"""Mode démonstration (ADR-071) : l'API et un VRAI worker, deux process, sans RAGFlow ni GPU.

C'est la topologie exacte du chemin d'essai léger (`scripts/quickstart.sh demo`) :
une API (ici, l'application ASGI du test), un worker Celery lancé dans **un autre
process** sur la file `default`, un vrai broker Redis, une vraie base PostgreSQL.
Aucune tâche n'est exécutée en mémoire ni appelée à la main : l'API publie, le
worker consomme, les étapes s'enchaînent comme en production.

La seule chose qui relie les deux process est l'état du moteur factice. Le test
**échoue donc si ce partage est cassé** :

- avec `fake-shared`, l'ingestion atteint `completed` et la recherche, servie par
  l'API, retrouve un texte que seul le worker a indexé ;
- avec `fake` (état propre au process, ADR-070), la même recherche ne retrouve rien —
  c'est le piège que `fake-shared` résout, et la preuve que le test discrimine.

Redis : les bases 12 à 14 du Redis de développement, réservées à ce module et
vidées avant et après (le rate limiting réel utilise la 9, la stack de
développement les 0 à 2).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from http import HTTPStatus
from pathlib import Path

import pytest
import redis as redis_sync
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
from app.services import documents as documents_service
from app.services import jobs as jobs_service
from app.workers.celery_app import app as celery_app
from app.workers.celery_app import task_by_name
from tests.conftest import _REDIS_HOST_PORT, BASE_URL
from tests.postgres import (
    BACKEND_DIR,
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
REDIS_STATE_DB, REDIS_BROKER_DB, REDIS_RESULT_DB = 14, 13, 12
WAIT_SECONDS = 90.0


def _redis_url(db: int) -> str:
    return f"redis://127.0.0.1:{_REDIS_HOST_PORT}/{db}"


def _flush_demo_redis() -> None:
    for db in (REDIS_STATE_DB, REDIS_BROKER_DB, REDIS_RESULT_DB):
        redis_sync.Redis.from_url(_redis_url(db)).flushdb()


@pytest.fixture(scope="module")
def demo_database(database_url: URL) -> Iterator[URL]:
    """Base dédiée au module : l'API et le worker y commitent pour de vrai."""
    try:
        redis_sync.Redis.from_url(_redis_url(REDIS_STATE_DB)).ping()
    except redis_sync.RedisError:
        pytest.skip("Redis de développement injoignable : démarrer la stack Docker.")
    url = resolve_test_database_url(f"{database_url.database}_demo_mode")
    recreate_database(url)
    run_upgrade(url)
    try:
        yield url
    finally:
        drop_database(url)


@pytest.fixture(params=["fake-shared", "fake"])
def engine_id(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def demo_stack(
    app: FastAPI,
    demo_database: URL,
    tmp_path: Path,
    engine_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[subprocess.Popen[bytes]]:
    """Configuration du mode démo, côté API (variables) et côté worker (process)."""
    _flush_demo_redis()
    settings_env = {
        "RAGENGINE_ENV": "development",
        "INGESTION_ENGINE": engine_id,
        "POSTGRES_HOST": demo_database.host or "127.0.0.1",
        "POSTGRES_PORT": str(demo_database.port),
        "POSTGRES_DB": str(demo_database.database),
        "POSTGRES_USER": str(demo_database.username),
        "POSTGRES_PASSWORD": str(demo_database.password),
        "REDIS_URL": _redis_url(REDIS_STATE_DB),
        "CELERY_BROKER_URL": _redis_url(REDIS_BROKER_DB),
        "CELERY_RESULT_BACKEND": _redis_url(REDIS_RESULT_DB),
        "RAGENGINE_STORAGE_ROOT_DIR": str(tmp_path / "documents"),
        "RAGFLOW_POLL_INTERVAL_SECONDS": "1",
        "RAGENGINE_RATE_LIMIT_ENABLED": "false",
    }
    del app  # l'application est déjà construite ; ses réglages sont relus par requête
    for name, value in settings_env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    reset_shared_fake_engine()
    # L'API publie pour de vrai (le garde-fou `dispatched_tasks` est levé), vers le broker du test.
    monkeypatch.setattr(documents_service, "task_by_name", task_by_name)
    monkeypatch.setattr(jobs_service, "task_by_name", task_by_name)
    monkeypatch.setattr(celery_app.conf, "broker_url", _redis_url(REDIS_BROKER_DB))
    monkeypatch.setattr(celery_app.conf, "result_backend", _redis_url(REDIS_RESULT_DB))

    log = tmp_path / "worker.log"
    with log.open("wb") as sink:
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "app.workers.celery_app",
                "worker",
                "--pool=solo",
                "--queues=default",
                "--loglevel=WARNING",
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, **settings_env},
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    try:
        yield worker
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=20)
        except subprocess.TimeoutExpired:
            worker.kill()
        _flush_demo_redis()
        get_settings.cache_clear()
        reset_shared_fake_engine()


@pytest.fixture
async def demo_client(
    app: FastAPI, demo_stack: subprocess.Popen[bytes], demo_database: URL, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(demo_database, poolclass=NullPool)
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
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            yield client
    finally:
        await engine.dispose()


async def _wait_for_job(
    client: AsyncClient, worker: subprocess.Popen[bytes], job_id: str, states: set[str]
) -> dict[str, object]:
    """Interroge le job jusqu'à l'un des `states` ; échoue si le worker meurt ou le délai passe."""
    deadline = time.monotonic() + WAIT_SECONDS
    job: dict[str, object] = {}
    while time.monotonic() < deadline:
        job = (await client.get(f"{API_PREFIX}/jobs/{job_id}")).json()
        if job["state"] in states:
            return job
        assert worker.poll() is None, "le worker s'est arrêté"
        await asyncio.sleep(0.5)
    pytest.fail(f"job resté à « {job.get('state')} » après {WAIT_SECONDS:.0f} s : {job}")


async def _run_journey(client: AsyncClient, worker: subprocess.Popen[bytes]) -> tuple[str, str]:
    email = f"demo-{uuid.uuid4().hex[:10]}@example.com"
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

    # Le precheck tourne dans le worker ; la validation humaine relance le reste du pipeline.
    await _wait_for_job(client, worker, job_id, {"awaiting_review"})
    decided = await client.put(
        f"{API_PREFIX}/documents/{document_id}/precheck/decision", json={"decision": {}}
    )
    assert decided.status_code == HTTPStatus.OK, decided.text
    job = await _wait_for_job(client, worker, job_id, {"completed", "failed"})
    assert job["state"] == "completed", job
    return base_id, job_id


async def test_search_in_the_api_finds_what_the_worker_indexed(
    demo_client: AsyncClient, demo_stack: subprocess.Popen[bytes], engine_id: str
) -> None:
    base_id, _ = await _run_journey(demo_client, demo_stack)
    found = await demo_client.post(
        f"{API_PREFIX}/retrieval/search",
        json={"knowledge_base_id": base_id, "query": "délai de remboursement"},
    )
    if engine_id == "fake":
        # Le piège d'ADR-070, vérifié : le worker a bien indexé, mais l'état du moteur est
        # propre à son process — l'API, qui répond à la recherche, n'en voit rien. Ce cas
        # est la contre-épreuve : s'il se mettait à retrouver le texte, le test du moteur
        # partagé ne prouverait plus rien.
        contents = [r["content"] for r in found.json().get("results", [])]
        assert found.status_code != HTTPStatus.OK or "trente jours" not in " ".join(contents)
        return

    assert found.status_code == HTTPStatus.OK, found.text
    contents = [result["content"] for result in found.json()["results"]]
    assert contents[0] == "Le délai de remboursement est de trente jours."
