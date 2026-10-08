"""Ordre commit ↔ publication Celery : la seule chose qui rende le pipeline correct.

Ces tests ne vérifient **pas** qu'une tâche a été publiée avec le bon argument :
ils vérifient qu'au moment précis où elle est publiée, l'état qu'elle va relire
est déjà visible depuis une **autre connexion**. C'est exactement ce qu'un worker
Celery fait — il ouvre sa propre session, dans son propre process — et c'est la
seule façon de distinguer un dispatch correct d'un dispatch prématuré.

Pourquoi cette distinction est vitale, et pourquoi un mock ordinaire la manque :

- une tâche publiée avant le `COMMIT` est décrochée en quelques millisecondes par
  un worker qui, sous READ COMMITTED, lit l'état **précédent** ;
- l'échec est alors totalement silencieux — pas d'exception, pas de log : juste un
  `run_precheck` sur un job qui n'existe pas encore, ou un `run_chunking` qui
  découpe avec le profil par défaut en ignorant la décision de l'utilisateur ;
- un test qui remplace la fonction de publication entière ne peut rien voir de
  tout cela, puisqu'il supprime précisément le code dont l'instant d'exécution
  est en cause.

Ces tests ont donc besoin d'une session qui **commite pour de vrai**, et non de la
fixture `db_session` (transaction externe + points de sauvegarde), dont aucun
commit n'est jamais visible d'ailleurs.

D'où la **base dédiée** au module (même procédé que `tests/db/test_migrations.py`
pour son cycle de migrations) : des commits réels dans la base partagée du reste
de la suite y laisseraient des lignes que les assertions de comptage global des
autres modules verraient passer. Ici, rien n'est partagé — la base est créée,
migrée, puis détruite avec le module.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.dependencies import get_storage
from app.api.v1.routes import auth, documents, evaluations, jobs, knowledge_bases
from app.db.models import Document, EvaluationRun, IngestionJob, KnowledgeBase
from app.db.session import get_db_session
from app.domain.enums import DocumentStatus, EvaluationRunStatus, JobState
from app.domain.schemas.evaluation import MAX_EVALUATION_SAMPLE_SIZE
from app.domain.schemas.ingestion_job import JobError
from app.main import API_PREFIX
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.llm.base import ChatMessage, LLMCapabilities
from app.providers.storage import LocalFilesystemStorageAdapter, StoragePort
from app.services import documents as documents_service
from app.services import evaluations as evaluations_service
from app.services import jobs as jobs_service
from app.services.jobs import IngestionJobService
from app.workers.celery_app import CHUNKING_TASK, PRECHECK_TASK, RUN_ASYNC_EVALUATION_TASK
from tests.api.v1.test_knowledge_base_routes import PASSWORD, file_bytes
from tests.conftest import BASE_URL
from tests.postgres import (
    drop_database,
    recreate_database,
    resolve_test_database_url,
    run_upgrade,
)

KB_URL = f"{API_PREFIX}/knowledge-bases"


@dataclass(slots=True)
class Publication:
    """Une publication Celery, et ce qu'une connexion tierce voyait à cet instant."""

    task_name: str
    job_id: str
    visible: dict[str, Any] | None
    """Ligne relue depuis une connexion indépendante, ou `None` si rien n'était visible."""


@dataclass(slots=True)
class DispatchProbe:
    """Substitut de `task_by_name` qui sonde la base au moment exact de la publication.

    La sonde tourne dans un **thread séparé**, avec sa propre boucle et sa propre
    connexion : `delay` est appelée depuis du code synchrone au cœur de la requête,
    et c'est précisément là qu'il faut regarder. Un `SELECT` ne bloque jamais sur
    une écriture non commitée sous PostgreSQL — il lit la dernière version
    commitée, ce qui est exactement la question posée.
    """

    url: URL
    publications: list[Publication] = field(default_factory=list)

    def __call__(self, task_name: str) -> DispatchProbe:
        self._pending = task_name
        return self

    def delay(self, job_id: str) -> None:
        self.publications.append(
            Publication(
                task_name=self._pending,
                job_id=job_id,
                visible=_read_from_an_independent_connection(self.url, uuid.UUID(job_id)),
            )
        )

    _pending: str = ""

    @property
    def names(self) -> list[str]:
        """Noms des tâches publiées, dans l'ordre."""
        return [publication.task_name for publication in self.publications]


def _read_from_an_independent_connection(url: URL, job_id: uuid.UUID) -> dict[str, Any] | None:
    """Relit le job et son document depuis une connexion neuve, comme le ferait un worker."""

    async def _read() -> dict[str, Any] | None:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(bind=engine) as session:
                job = await session.get(IngestionJob, job_id)
                if job is None:
                    return None
                document = await session.get(Document, job.document_id)
                return {
                    "job_state": job.state,
                    "precheck_decision": document.precheck_decision if document else None,
                    "document_exists": document is not None,
                }
        finally:
            await engine.dispose()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _read()).result(timeout=30)


@pytest.fixture(scope="module")
def dispatch_database(database_url: URL) -> Iterator[URL]:
    """Base dédiée au module, créée et migrée pour lui seul, détruite avec lui.

    Les tests d'ici commitent réellement : partager la base des autres modules
    leur ferait voir des lignes surnuméraires dans leurs comptages globaux, selon
    l'ordre d'exécution. Une base à part supprime la question.
    """
    url = resolve_test_database_url(f"{database_url.database}_dispatch")
    recreate_database(url)
    run_upgrade(url)
    try:
        yield url
    finally:
        drop_database(url)


@pytest.fixture
def probe(dispatch_database: URL, monkeypatch: pytest.MonkeyPatch) -> DispatchProbe:
    """Remplace **la seule** résolution de tâche du service, pas la logique autour."""
    installed = DispatchProbe(url=dispatch_database)
    monkeypatch.setattr(documents_service, "task_by_name", installed)
    return installed


@pytest.fixture
def jobs_probe(dispatch_database: URL, monkeypatch: pytest.MonkeyPatch) -> DispatchProbe:
    """Même sonde que `probe`, mais posée sur `IngestionJobService.retry` (ADR-020)."""
    installed = DispatchProbe(url=dispatch_database)
    monkeypatch.setattr(jobs_service, "task_by_name", installed)
    return installed


@dataclass(slots=True)
class EvaluationPublication:
    """Même rôle que `Publication`, pour un `EvaluationRun` plutôt qu'un job d'ingestion."""

    task_name: str
    knowledge_base_id: str
    evaluation_run_id: str
    visible: dict[str, Any] | None


@dataclass(slots=True)
class EvaluationDispatchProbe:
    """Même sonde que `DispatchProbe` (Phase 12 Étape 4, ADR-028), posée sur le run
    d'évaluation asynchrone plutôt que sur un job d'ingestion — voir `DispatchProbe`
    pour ce que ce détour (connexion tierce, thread séparé) prouve et pourquoi un
    mock ordinaire le manquerait.

    `delay` prend deux arguments positionnels, comme `run_async_evaluation`
    (`app.workers.tasks.evaluations`) l'exige réellement : la signature à un
    seul argument qu'elle portait avant a laissé passer un appelant qui ne
    fournissait que `evaluation_run_id`, sans jamais lever — un double
    maison n'est jamais soumis à la validation d'arité réelle de Celery
    (`Task.apply_async` → `check_arguments`), c'est cet écart d'arité que
    Celery a fini par relever en campagne E2E (`TypeError: run_async_
    evaluation() missing 1 required positional argument`). Une arité fausse
    ici referait échouer ce fichier de tests avec le code corrigé, ce qui est
    exactement le filet qu'on veut : elle doit rester synchronisée avec le
    site d'appel réel (`EvaluationService._publish_after_commit`).
    """

    url: URL
    publications: list[EvaluationPublication] = field(default_factory=list)

    def __call__(self, task_name: str) -> EvaluationDispatchProbe:
        self._pending = task_name
        return self

    def delay(self, knowledge_base_id: str, evaluation_run_id: str) -> None:
        self.publications.append(
            EvaluationPublication(
                task_name=self._pending,
                knowledge_base_id=knowledge_base_id,
                evaluation_run_id=evaluation_run_id,
                visible=_read_evaluation_run_from_an_independent_connection(
                    self.url, uuid.UUID(evaluation_run_id)
                ),
            )
        )

    _pending: str = ""

    @property
    def names(self) -> list[str]:
        return [publication.task_name for publication in self.publications]


def _read_evaluation_run_from_an_independent_connection(
    url: URL, evaluation_run_id: uuid.UUID
) -> dict[str, Any] | None:
    """Relit le run depuis une connexion neuve, comme le ferait le worker Celery réel."""

    async def _read() -> dict[str, Any] | None:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(bind=engine) as session:
                run = await session.get(EvaluationRun, evaluation_run_id)
                if run is None:
                    return None
                return {"status": run.status, "sample_size": run.sample_size}
        finally:
            await engine.dispose()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _read()).result(timeout=30)


@pytest.fixture
def evaluation_probe(
    dispatch_database: URL, monkeypatch: pytest.MonkeyPatch
) -> EvaluationDispatchProbe:
    """Remplace la résolution de tâche d'`EvaluationService`, comme `probe` fait pour
    `DocumentService`."""
    installed = EvaluationDispatchProbe(url=dispatch_database)
    monkeypatch.setattr(evaluations_service, "task_by_name", installed)
    return installed


@dataclass(slots=True)
class _NoopEvaluationLLM:
    """Double minimal, jamais appelé au-delà de sa résolution (validation d'entrée seule)."""

    capabilities: LLMCapabilities = field(
        default_factory=lambda: LLMCapabilities(
            provider_id="fake-eval-llm",
            vendor="anthropic",
            context_window=100_000,
            supports_streaming=True,
        )
    )

    async def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> str:  # pragma: no cover - non exercé
        raise NotImplementedError

    def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:  # pragma: no cover - non exercé
        raise NotImplementedError


@pytest.fixture
def evaluation_resolvers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Substitue moteur et fournisseur LLM d'`EvaluationService` : `RAGFLOW_API_KEY` est vide
    en test, et aucune génération n'a lieu avant l'exécution Celery (interceptée à côté)."""
    monkeypatch.setattr(
        evaluations_service,
        "resolve_ingestion_engine",
        lambda settings: FakeIngestionEngine(
            strict=False,
        ),
    )
    monkeypatch.setattr(
        evaluations_service,
        "resolve_llm_provider",
        lambda provider_id, settings, *, override_api_key=None: _NoopEvaluationLLM(),
    )


async def _enable_evaluation_on_an_indexed_document(
    url: URL, *, knowledge_base_id: str, document_id: str
) -> None:
    """Active `evaluation_enabled` et fait passer un document à `INDEXED`, directement en base.

    Court-circuite délibérément le pipeline Celery (désactivé par les sondes de
    ce module) : seul l'état final compte pour ce test, pas comment on y
    arrive — même raccourci que `_fail_job` pour un job d'ingestion.
    """
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with AsyncSession(bind=engine) as session:
            knowledge_base = await session.get(KnowledgeBase, uuid.UUID(knowledge_base_id))
            assert knowledge_base is not None
            knowledge_base.evaluation_enabled = True
            knowledge_base.ragflow_dataset_external_id = "dataset-externe"

            document = await session.get(Document, uuid.UUID(document_id))
            assert document is not None
            document.status = DocumentStatus.INDEXED
            document.ragflow_document_external_id = "document-externe"

            await session.commit()
    finally:
        await engine.dispose()


async def _fail_job(
    url: URL, job_id: str, *, retry_state: JobState = JobState.QUEUED, retryable: bool = True
) -> None:
    """Fait échouer un job directement en base, comme le ferait une tâche Celery réelle.

    Aucune route HTTP ne fait volontairement passer un job à `failed` (c'est le
    pipeline d'ingestion, pas un client, qui décide d'un échec) : ce raccourci
    reproduit exactement ce que `app.workers.tasks.ingestion._fail` écrit,
    jusqu'à la structure de `JobError`.
    """
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with AsyncSession(bind=engine) as session:
            job = await session.get(IngestionJob, uuid.UUID(job_id))
            assert job is not None
            await IngestionJobService(session).transition(
                job,
                JobState.FAILED,
                error=JobError(
                    code="engine_transient",
                    message="Panne réseau du moteur d'ingestion.",
                    retryable=retryable,
                    retry_state=retry_state,
                ),
            )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.fixture
async def committing_app(
    app: FastAPI, dispatch_database: URL, tmp_path: Path
) -> AsyncIterator[FastAPI]:
    """Application dont la session **commite réellement**, comme en production.

    La fixture `db_session` du reste de la suite travaille en points de sauvegarde
    dans une transaction jamais commitée : parfaite pour isoler un test, inutile
    ici, puisque c'est précisément la visibilité d'un commit qu'on mesure.
    """
    engine = create_async_engine(dispatch_database, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)
    storage = LocalFilesystemStorageAdapter(root=tmp_path / "documents")

    async def _session() -> AsyncIterator[AsyncSession]:
        """Même politique que `get_db_session` : commit en sortie, rollback sinon."""
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

    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(knowledge_bases.router, prefix=API_PREFIX)
    app.include_router(documents.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    app.include_router(evaluations.router, prefix=API_PREFIX)
    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_storage] = _storage
    try:
        yield app
    finally:
        await engine.dispose()


@pytest.fixture
async def committing_client(committing_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client HTTP conservant ses cookies, branché sur l'application qui commite."""
    transport = ASGITransport(app=committing_app)
    async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


async def _signed_in(client: AsyncClient) -> None:
    """Inscrit puis connecte un compte réel dans la base dédiée du module."""
    email = f"dispatch-{uuid.uuid4().hex[:12]}@example.com"
    created = await client.post(
        f"{API_PREFIX}/users",
        json={"email": email, "password": PASSWORD, "display_name": "Amara"},
    )
    assert created.status_code == HTTPStatus.CREATED, created.text
    session = await client.post(
        f"{API_PREFIX}/sessions", json={"email": email, "password": PASSWORD}
    )
    assert session.status_code == HTTPStatus.CREATED, session.text


async def _create_base(client: AsyncClient) -> dict[str, Any]:
    """Crée une base par HTTP et renvoie sa représentation."""
    response = await client.post(
        KB_URL, json={"name": "Référentiel Qualité", "embedding_model_id": "bge-m3"}
    )
    assert response.status_code == HTTPStatus.CREATED, response.text
    return dict(response.json())


async def _ingest(client: AsyncClient, base_id: str) -> dict[str, Any]:
    """Téléverse un document et renvoie sa représentation."""
    response = await client.post(
        f"{KB_URL}/{base_id}/documents/ingest",
        files={"file": ("rapport.pdf", file_bytes("rapport.pdf"), "application/pdf")},
    )
    assert response.status_code == HTTPStatus.ACCEPTED, response.text
    return dict(response.json())


# ─── Bug A : l'ingestion doit réellement démarrer le pipeline ────────────────


async def test_ingesting_a_document_publishes_the_precheck(
    committing_client: AsyncClient, probe: DispatchProbe
) -> None:
    """Sans cette publication, un document téléversé resterait `queued` pour toujours."""
    await _signed_in(committing_client)
    base = await _create_base(committing_client)

    document = await _ingest(committing_client, base["id"])

    assert probe.names == [PRECHECK_TASK]
    assert probe.publications[0].job_id == document["current_job_id"]


async def test_the_job_is_committed_before_the_precheck_is_published(
    committing_client: AsyncClient, probe: DispatchProbe
) -> None:
    """Un worker qui décroche aussitôt doit trouver le job, pas un `NotFoundError`."""
    await _signed_in(committing_client)
    base = await _create_base(committing_client)

    await _ingest(committing_client, base["id"])

    observed = probe.publications[0].visible
    assert observed is not None, "le job n'était pas encore visible d'une autre connexion"
    assert observed["document_exists"] is True
    assert observed["job_state"] is JobState.QUEUED


# ─── Bug B : la décision doit être commitée avant de réveiller le découpage ──


async def test_deciding_the_precheck_publishes_the_chunking(
    committing_client: AsyncClient, probe: DispatchProbe
) -> None:
    """L'aiguillage humain est le point de reprise du pipeline."""
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])

    response = await committing_client.put(
        f"{API_PREFIX}/documents/{document['id']}/precheck/decision",
        json={"decision": {"optimize_for_tables": True, "heavy_ocr": True}},
    )

    assert response.status_code == HTTPStatus.OK
    assert probe.names == [PRECHECK_TASK, CHUNKING_TASK]
    assert probe.publications[-1].job_id == document["current_job_id"]


async def test_the_decision_is_committed_before_the_chunking_is_published(
    committing_client: AsyncClient, probe: DispatchProbe
) -> None:
    """La régression que ce module existe pour attraper.

    Publier avant le commit ne lève rien : le worker lit simplement `NULL` et
    découpe avec le profil par défaut. La décision de l'utilisateur disparaît
    sans trace — d'où la vérification depuis une connexion indépendante.
    """
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])

    await committing_client.put(
        f"{API_PREFIX}/documents/{document['id']}/precheck/decision",
        json={"decision": {"optimize_for_tables": True, "heavy_ocr": True}},
    )

    observed = probe.publications[-1].visible
    assert observed is not None
    assert observed["precheck_decision"] == {
        "optimize_for_tables": True,
        "heavy_ocr": True,
        "use_parent_child": False,
        "chunk_method_override": None,
    }


async def test_the_committed_decision_survives_the_request(
    committing_client: AsyncClient, probe: DispatchProbe
) -> None:
    """Le commit anticipé ne doit rien casser : la donnée reste en base après la requête."""
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])

    await committing_client.put(
        f"{API_PREFIX}/documents/{document['id']}/precheck/decision",
        json={"decision": {"optimize_for_tables": False, "heavy_ocr": True}},
    )

    read = await committing_client.get(f"{API_PREFIX}/documents/{document['id']}")
    assert read.json()["precheck_decision"] == {
        "optimize_for_tables": False,
        "heavy_ocr": True,
        "use_parent_child": False,
        "chunk_method_override": None,
    }


async def test_a_replayed_upload_still_publishes_the_precheck(
    committing_client: AsyncClient, probe: DispatchProbe
) -> None:
    """Un dispatch perdu (broker indisponible) se rattrape au rejeu du téléversement.

    La garde d'idempotence de `run_precheck` rend la republication inoffensive sur
    un job déjà parti ; ne rien republier laisserait en revanche un document
    définitivement `queued` après une panne du broker.
    """
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    first = await _ingest(committing_client, base["id"])

    replay = await _ingest(committing_client, base["id"])

    assert replay["id"] == first["id"]
    assert probe.names == [PRECHECK_TASK, PRECHECK_TASK]


async def test_the_precheck_is_published_after_the_response_is_committed(
    committing_client: AsyncClient, probe: DispatchProbe
) -> None:
    """Le document lui-même, et pas seulement son job, doit être visible d'ailleurs."""
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])

    engine = create_async_engine(probe.url, poolclass=NullPool)
    try:
        async with AsyncSession(bind=engine) as session:
            rows = await session.execute(
                select(Document).where(Document.id == uuid.UUID(document["id"]))
            )
            assert len(rows.scalars().all()) == 1
    finally:
        await engine.dispose()


# ─── Bug C : la reprise d'un job en échec doit republier après son commit (ADR-020) ──


async def test_retrying_a_job_publishes_the_task_owning_its_source_state(
    committing_client: AsyncClient, jobs_probe: DispatchProbe
) -> None:
    """Sans cette publication, un job repris resterait `queued` pour toujours."""
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])
    job_id = document["current_job_id"]
    await _fail_job(jobs_probe.url, job_id, retry_state=JobState.QUEUED)

    response = await committing_client.post(f"{API_PREFIX}/jobs/{job_id}/retry")

    assert response.status_code == HTTPStatus.ACCEPTED, response.text
    assert response.json()["state"] == "queued"
    assert jobs_probe.names == [PRECHECK_TASK]
    assert jobs_probe.publications[0].job_id == job_id


async def test_the_retry_is_committed_before_the_task_is_published(
    committing_client: AsyncClient, jobs_probe: DispatchProbe
) -> None:
    """Même bug que Bug A/B, sur un troisième point de déclenchement : la reprise.

    Un worker qui décroche `run_precheck` aussitôt publié doit trouver le job
    déjà `queued`, `attempts` déjà incrémenté — pas l'état `failed` d'avant la
    requête.
    """
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])
    job_id = document["current_job_id"]
    await _fail_job(jobs_probe.url, job_id, retry_state=JobState.QUEUED)

    response = await committing_client.post(f"{API_PREFIX}/jobs/{job_id}/retry")
    assert response.status_code == HTTPStatus.ACCEPTED, response.text

    observed = jobs_probe.publications[0].visible
    assert observed is not None, "le job n'était pas encore visible d'une autre connexion"
    assert observed["job_state"] is JobState.QUEUED


async def test_retrying_a_non_retryable_failure_is_refused_without_publishing(
    committing_client: AsyncClient, jobs_probe: DispatchProbe
) -> None:
    """`409` sans `force=true` : rien ne doit être publié sur une cause non reprenable."""
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])
    job_id = document["current_job_id"]
    await _fail_job(jobs_probe.url, job_id, retryable=False)

    response = await committing_client.post(f"{API_PREFIX}/jobs/{job_id}/retry")

    assert response.status_code == HTTPStatus.CONFLICT, response.text
    assert jobs_probe.names == []


async def test_forcing_a_non_retryable_retry_still_publishes_after_the_commit(
    committing_client: AsyncClient, jobs_probe: DispatchProbe
) -> None:
    """`force=true` reprend quand même — toujours après le commit, jamais avant."""
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])
    job_id = document["current_job_id"]
    await _fail_job(jobs_probe.url, job_id, retryable=False, retry_state=JobState.QUEUED)

    response = await committing_client.post(f"{API_PREFIX}/jobs/{job_id}/retry?force=true")

    assert response.status_code == HTTPStatus.ACCEPTED, response.text
    assert jobs_probe.names == [PRECHECK_TASK]
    observed = jobs_probe.publications[0].visible
    assert observed is not None
    assert observed["job_state"] is JobState.QUEUED


async def test_retrying_a_job_not_in_failed_state_answers_conflict(
    committing_client: AsyncClient, jobs_probe: DispatchProbe
) -> None:
    """Rien à reprendre sur un job encore `queued` : `409`, aucune publication."""
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])

    response = await committing_client.post(f"{API_PREFIX}/jobs/{document['current_job_id']}/retry")

    assert response.status_code == HTTPStatus.CONFLICT, response.text
    assert jobs_probe.names == []


# ─── Bug C : le run d'évaluation asynchrone doit être visible avant sa publication ──
# Phase 12 Étape 4 (item restant), ADR-028 — même invariant que les bugs A/B
# ci-dessus, sur un troisième point de couture commit/dispatch du dépôt.


async def test_creating_a_large_evaluation_run_publishes_it_after_the_commit(
    committing_client: AsyncClient,
    evaluation_probe: EvaluationDispatchProbe,
    evaluation_resolvers: None,
) -> None:
    """Un worker qui décroche `run_async_evaluation` aussitôt doit trouver le run, pas `None`.

    Sans le commit explicite d'`EvaluationService._publish_after_commit` avant
    `task_by_name(...).delay(...)`, un worker qui décroche la tâche en
    quelques millisecondes, sur sa propre connexion, sous READ COMMITTED, ne
    verrait pas encore le run — `run_pending_evaluation` le traiterait comme
    disparu (`None`, course bénigne) et ne produirait jamais aucun item,
    silencieusement : un run `pending` bloqué pour toujours, exactement le
    défaut que ce test verrouille (même famille que les bugs A/B ci-dessus,
    CONTRIBUTING.md §9).
    """
    await _signed_in(committing_client)
    base = await _create_base(committing_client)
    document = await _ingest(committing_client, base["id"])
    await _enable_evaluation_on_an_indexed_document(
        evaluation_probe.url, knowledge_base_id=base["id"], document_id=document["id"]
    )

    response = await committing_client.post(
        f"{KB_URL}/{base['id']}/evaluations",
        json={
            "sample_size": MAX_EVALUATION_SAMPLE_SIZE + 1,
            "llm_provider_id": "claude-opus-5",
        },
    )

    assert response.status_code == HTTPStatus.ACCEPTED, response.text
    assert evaluation_probe.names == [RUN_ASYNC_EVALUATION_TASK]
    publication = evaluation_probe.publications[0]
    assert publication.knowledge_base_id == base["id"]
    observed = publication.visible
    assert observed is not None, "le run n'était pas encore visible d'une autre connexion"
    assert observed["status"] is EvaluationRunStatus.PENDING
    assert observed["sample_size"] == 1
