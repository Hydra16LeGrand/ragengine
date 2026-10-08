"""Fixtures des tâches Celery : exécution en mémoire, base réelle, jeu de données commité.

Ces tests sont **synchrones**, contrairement au reste de la suite, parce qu'une
tâche Celery l'est : elle ouvre elle-même sa boucle d'événements (`asyncio.run`)
et sa propre connexion. Deux conséquences directes :

- la fixture `db_session`, transactionnelle et rattachée à la boucle du test,
  est inutilisable ici — la tâche ne verrait rien d'une transaction non
  commitée ;
- le jeu de données est donc **commité**, puis effacé en fin de test par la
  cascade `users → knowledge_bases → documents → ingestion_jobs → events`.

Aucun Redis n'est requis : le routage s'inspecte hors ligne et la traversée
complète s'exécute en mode `task_always_eager`.

**Ni moteur d'ingestion réel, ni disque.** Les deux briques externes du pipeline
sont remplacées par des doubles en mémoire (`FakeEngine`, `FakeStorage`) : la
suite doit rester verte sans RAGFlow, et surtout pouvoir **scénariser** des
états que l'instance réelle ne produit pas à la demande — un parsing figé, un
échec du moteur, une annulation venue d'ailleurs.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import URL, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Document, IngestionJob, IngestionJobEvent, KnowledgeBase, User
from app.domain.enums import DocumentStatus, JobState
from app.providers.engine.base import (
    EngineChunk,
    EngineParseStatus,
)
from app.providers.engine.fake import FakeIngestionEngine
from app.services.jobs import IngestionJobService
from app.workers import session as worker_session
from app.workers.celery_app import app as celery_app
from app.workers.tasks import ingestion
from tests.db.factories import make_document, make_knowledge_base, make_user
from tests.ingestion.precheck.documents import FRENCH_PARAGRAPH, text_pdf

SessionFactory = async_sessionmaker[AsyncSession]
"""Fabrique de sessions branchée sur la base de test."""

PDF_BYTES = text_pdf(pages=1)
"""Contenu par défaut servi par le stockage factice.

Un **vrai** PDF français à couche texte, fabriqué par la même usine que les
tests du precheck : le rapport produit par `run_precheck` est alors celui d'un
document plausible, langues détectées comprises, et non celui d'un octet-flux
que l'analyse aurait rejeté.
"""

DEFAULT_CHUNK_TEXT = " ".join(FRENCH_PARAGRAPH)
"""Contenu par défaut de `FakeEngine.chunks` — le même paragraphe que `PDF_BYTES`.

Fait coïncider volontairement le texte source (PDF) et le texte des fragments
pour que le chemin nominal des tests existants (qui ne portent aucune
assertion sur le garde-fou qualité, ADR-024) reste un cas *sain* de bout en
bout : bonne couverture source, aucun fragment dégénéré. Les tests dédiés au
garde-fou (`test_ingestion_quality_guard.py`) écrasent `fake_engine.chunks`
pour scénariser les cas dégradés."""

DATASET_ID = "dataset-externe"
DOCUMENT_ID = "document-externe"


def parse_status(
    run: str = "running",
    *,
    inferred_phase: JobState | None = None,
    progress: float = 0.0,
    last_message: str | None = None,
    chunk_count: int = 0,
    error: str | None = None,
) -> EngineParseStatus:
    """Projection d'état du moteur, telle que l'adaptateur la produirait."""
    return EngineParseStatus(
        run=run,  # type: ignore[arg-type]  # le littéral est vérifié par le modèle lui-même.
        inferred_phase=inferred_phase,
        progress=progress,
        last_message=last_message,
        chunk_count=chunk_count,
        error=error,
    )


DONE = parse_status("done", inferred_phase=JobState.COMPLETED, progress=1.0, last_message="Done.")
"""Document terminé côté moteur — le cas d'un petit document qui va vite."""


@dataclass(slots=True)
class FakeStorage:
    """Stockage en mémoire : `read` rend le contenu inscrit, ou le PDF par défaut.

    `failure` permet de simuler un volume injoignable — le seul cas où le
    precheck doit **échouer** au lieu de produire un rapport dégradé.
    """

    contents: dict[str, bytes] = field(default_factory=dict)
    default: bytes = PDF_BYTES
    reads: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    """URI par appel à `delete` — hors du pipeline d'ingestion, exercé par
    `tests/workers/test_maintenance.py` (`DocumentService.delete`)."""
    failure: Exception | None = None

    async def save(self, content: bytes, *, checksum_sha256: str, filename: str) -> str:
        uri = f"memory://{checksum_sha256}"
        self.contents[uri] = content
        return uri

    async def read(self, storage_uri: str) -> bytes:
        self.reads.append(storage_uri)
        if self.failure is not None:
            raise self.failure
        return self.contents.get(storage_uri, self.default)

    async def delete(self, storage_uri: str) -> None:
        self.deleted.append(storage_uri)
        self.contents.pop(storage_uri, None)


def make_fake_engine() -> FakeIngestionEngine:
    """Moteur d'ingestion factice du pipeline : états scénarisés, journal d'appels.

    Mêmes valeurs par défaut qu'avant son remplacement par `FakeIngestionEngine` :
    identifiants fixes, un fragment sain par défaut, et un sondage qui rend `DONE`.
    Le dernier état de `statuses` est **rejoué indéfiniment** : un scénario n'a besoin
    de décrire que ses transitions, pas de compter les sondages que la tâche fera.
    """
    return FakeIngestionEngine(
        strict=False,
        dataset_id=DATASET_ID,
        document_id=DOCUMENT_ID,
        statuses=[DONE],
        chunks=[EngineChunk(chunk_id="chunk-1", content=DEFAULT_CHUNK_TEXT)],
    )


@dataclass(slots=True)
class Publications:
    """Ce que les tâches ont publié, au lieu de le publier pour de vrai.

    Indispensable pour les étapes de sondage : en mode `task_always_eager`, une
    replanification s'exécuterait **immédiatement** et boucherait le test au lieu
    de simplement le renseigner.
    """

    successors: list[str] = field(default_factory=list)
    reschedules: list[str] = field(default_factory=list)


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> FakeIngestionEngine:
    """Substitue le moteur d'ingestion du pipeline par un double scénarisable."""
    engine = make_fake_engine()
    monkeypatch.setattr(ingestion, "_engine", lambda: engine)
    return engine


@pytest.fixture
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    """Substitue le stockage des fichiers d'origine par un double en mémoire."""
    storage = FakeStorage()
    monkeypatch.setattr(ingestion, "_storage", lambda: storage)
    return storage


@pytest.fixture
def publications(monkeypatch: pytest.MonkeyPatch) -> Publications:
    """Intercepte les publications Celery : rien ne part, tout est observable."""
    recorded = Publications()

    def _successor(step: ingestion.Step, job_id: uuid.UUID) -> None:
        if step.successor is not None:
            recorded.successors.append(step.successor)

    def _reschedule(step: ingestion.Step, job_id: uuid.UUID) -> None:
        recorded.reschedules.append(step.name)

    monkeypatch.setattr(ingestion, "_dispatch_successor", _successor)
    monkeypatch.setattr(ingestion, "_reschedule", _reschedule)
    return recorded


@dataclass(frozen=True, slots=True)
class SeededJob:
    """Job réellement commité en base, prêt à être traversé par les tâches."""

    job_id: uuid.UUID
    document_id: uuid.UUID
    owner_id: uuid.UUID

    @property
    def argument(self) -> str:
        """Identifiant sous la forme reçue par les tâches (sérialisation JSON)."""
        return str(self.job_id)


def run_sync[R](factory: SessionFactory, handler: Callable[[AsyncSession], Awaitable[R]]) -> R:
    """Déroule une coroutine dans sa propre boucle, exactement comme une tâche."""

    async def _run() -> R:
        async with factory() as db:
            result = await handler(db)
            await db.commit()
            return result

    return asyncio.run(_run())


@pytest.fixture
def eager_celery() -> Iterator[None]:
    """`task_always_eager` : les tâches s'exécutent en mémoire, sans broker.

    Équivaut à `CELERY_TASK_ALWAYS_EAGER=True` sans dépendre de l'environnement :
    l'application Celery est un singleton construit à l'import, une variable
    posée après coup n'aurait aucun effet sur elle.
    """
    conf = celery_app.conf
    previous = (conf.task_always_eager, conf.task_eager_propagates)
    conf.task_always_eager = True
    conf.task_eager_propagates = True
    try:
        yield
    finally:
        conf.task_always_eager, conf.task_eager_propagates = previous


@pytest.fixture
def worker_sessions(
    migrated_database: URL, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SessionFactory]:
    """Branche les tâches sur la base de test, en conservant leur modèle de session.

    `NullPool` comme en production : chaque `asyncio.run` d'une tâche ouvre et
    referme sa connexion, sans jamais en rendre une à un pool rattaché à une
    boucle d'événements déjà close.
    """
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)
    monkeypatch.setattr(worker_session, "get_session_factory", lambda: factory)
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())


async def _seed(db: AsyncSession) -> SeededJob:
    """Crée un propriétaire, sa base, un document et le job `queued` qui en découle."""
    owner = make_user()
    db.add(owner)
    await db.flush([owner])

    knowledge_base = make_knowledge_base(owner, slug=f"workers-{uuid.uuid4().hex[:8]}")
    db.add(knowledge_base)
    # `flush()` non restreint, pas `flush([knowledge_base])` : l'organisation
    # auto-assignée par la factory (`tests.db.factories.make_knowledge_base`,
    # ADR-022) n'est associée que via la relation ORM, jamais ajoutée
    # explicitement à la session. `flush(objects=[...])` ne cascade pas sur les
    # objets hors de la liste fournie, ce qui laisserait `organization_id`
    # NULL à l'INSERT (violation de contrainte constatée à l'exécution réelle).
    await db.flush()

    document = make_document(knowledge_base, filename=f"{uuid.uuid4().hex[:8]}.pdf")
    db.add(document)
    await db.flush([document])

    job = await IngestionJobService(db).open_for(document)
    return SeededJob(job_id=job.id, document_id=document.id, owner_id=owner.id)


async def _purge(db: AsyncSession, owner_id: uuid.UUID) -> None:
    """Efface le propriétaire : la cascade emporte base, document, job et journal."""
    await db.execute(delete(User).where(User.id == owner_id))


@pytest.fixture
def queued_job(worker_sessions: SessionFactory) -> Iterator[SeededJob]:
    """Job commité dans l'état initial `queued`, effacé en fin de test."""
    seeded = run_sync(worker_sessions, _seed)
    try:
        yield seeded
    finally:
        run_sync(worker_sessions, lambda db: _purge(db, seeded.owner_id))


def state_of(factory: SessionFactory, job_id: uuid.UUID) -> JobState:
    """Relit l'état du job directement en base, sans passer par l'API."""

    async def _read(db: AsyncSession) -> JobState:
        return (await IngestionJobService(db).get(job_id)).state

    return run_sync(factory, _read)


def job_of(factory: SessionFactory, job_id: uuid.UUID) -> IngestionJob:
    """Relit le job complet, tel que le sérialiserait `GET /api/v1/jobs/{id}`."""

    async def _read(db: AsyncSession) -> IngestionJob:
        return await IngestionJobService(db).get(job_id)

    return run_sync(factory, _read)


def journal_of(factory: SessionFactory, job_id: uuid.UUID) -> list[tuple[JobState, str | None]]:
    """Journal des transitions du job, dans l'ordre chronologique."""

    async def _read(db: AsyncSession) -> list[tuple[JobState, str | None]]:
        rows = (
            await db.execute(
                select(IngestionJobEvent)
                .where(IngestionJobEvent.job_id == job_id)
                .order_by(IngestionJobEvent.created_at)
            )
        ).scalars()
        return [(row.state, row.message) for row in rows]

    return run_sync(factory, _read)


def document_status_of(factory: SessionFactory, document_id: uuid.UUID) -> DocumentStatus:
    """Projection `Document.status`, écrite par le service de transition."""

    async def _read(db: AsyncSession) -> DocumentStatus:
        document = await db.get(Document, document_id)
        assert document is not None
        return document.status

    return run_sync(factory, _read)


def document_of(factory: SessionFactory, document_id: uuid.UUID) -> Document:
    """Relit le document complet (rapport de precheck, références du moteur…)."""

    async def _read(db: AsyncSession) -> Document:
        document = await db.get(Document, document_id)
        assert document is not None
        return document

    return run_sync(factory, _read)


def link_engine_refs(factory: SessionFactory, seeded: SeededJob) -> None:
    """Pose les références que `run_chunking` aurait écrites côté moteur.

    Les étapes de sondage les exigent : les fournir explicitement permet de
    tester chaque étape isolément, sans rejouer tout le pipeline en amont.
    """

    async def _apply(db: AsyncSession) -> None:
        document = await db.get(Document, seeded.document_id)
        assert document is not None
        knowledge_base = await db.get(KnowledgeBase, document.knowledge_base_id)
        assert knowledge_base is not None
        document.ragflow_document_external_id = DOCUMENT_ID
        knowledge_base.ragflow_dataset_external_id = DATASET_ID

    run_sync(factory, _apply)


def age_last_observation(factory: SessionFactory, job_id: uuid.UUID, *, seconds: int) -> None:
    """Recule l'instant de la dernière évolution observée, pour armer la veille.

    Écrit directement la colonne : c'est un témoin de vivacité, pas un état de
    job — il n'a pas d'écrivain unique à respecter, contrairement à `state`.
    """

    async def _apply(db: AsyncSession) -> None:
        job = await IngestionJobService(db).get(job_id)
        job.engine_progress_observed_at = datetime.now(UTC) - timedelta(seconds=seconds)

    run_sync(factory, _apply)


def _stepper(job_id: uuid.UUID, state: JobState) -> Callable[[AsyncSession], Awaitable[None]]:
    """Fabrique le handler d'une transition unique, pour l'exécuter isolément."""

    async def _apply(db: AsyncSession) -> None:
        service = IngestionJobService(db)
        job = await service.get(job_id)
        await service.transition(job, state, message=f"Préparation du test : {state.value}.")

    return _apply


def advance_to(factory: SessionFactory, job_id: uuid.UUID, *states: JobState) -> None:
    """Fait avancer un job par le service de transition — jamais par un `UPDATE` direct.

    Une transaction par état : `created_at` vient de `now()`, constant sur toute
    une transaction PostgreSQL, et le journal doit rester ordonnable.
    """
    for state in states:
        run_sync(factory, _stepper(job_id, state))
