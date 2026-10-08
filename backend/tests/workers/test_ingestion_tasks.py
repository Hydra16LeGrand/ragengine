"""Comportement des étapes bornées : traversée, travail réel, idempotence, échec.

Ce que ces tests verrouillent, contre une base PostgreSQL réelle :

- un job traverse toute la machine à états, et l'état final est celui que
  renverrait `GET /api/v1/jobs/{job_id}` ;
- le precheck **persiste réellement** son rapport, ses langues et sa pagination,
  et le chunking **engage réellement** le moteur (dataset, téléversement, profil) ;
- une tâche redistribuée après un crash ne produit ni exception ni doublon dans
  le journal — c'est la condition d'existence de `task_acks_late` ;
- un échec est visible **par l'utilisateur** (job `failed`, message dans le
  journal) *et* remonte à Celery pour sa politique de retry.

Le sondage du moteur a ses propres tests (`test_ingestion_polling.py`).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, KnowledgeBase, User
from app.domain.enums import DocumentStatus, JobState
from app.domain.schemas.document import PrecheckDecision
from app.domain.schemas.ingestion_job import IngestionJobRead
from app.ingestion.chunking_profile import (
    DOCLING_LAYOUT_RECOGNIZE,
    HEAVY_OCR_LAYOUT_RECOGNIZE,
    LAYOUT_RECOGNIZE_KEY,
    PLAIN_TEXT_LAYOUT_RECOGNIZE,
    TASK_PAGE_SIZE_KEY,
)
from app.providers.engine.base import (
    DEFAULT_CHUNK_METHOD,
    EngineDatasetRef,
    EngineError,
    EngineTransientError,
    EngineValidationError,
)
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.storage import StorageError
from app.services.jobs import IngestionJobService
from app.workers.celery_app import Task
from app.workers.tasks import ingestion
from app.workers.tasks.ingestion import (
    DEAD_LETTER_MARKER,
    Outcome,
    run_chunking,
    run_embedding,
    run_indexing,
    run_precheck,
)
from tests.db.factories import make_document, make_knowledge_base, make_user
from tests.workers.conftest import (
    DATASET_ID,
    DOCUMENT_ID,
    FakeStorage,
    Publications,
    SeededJob,
    SessionFactory,
    advance_to,
    document_of,
    document_status_of,
    job_of,
    journal_of,
    link_engine_refs,
    run_sync,
    state_of,
)

NOMINAL_PATH = [
    JobState.QUEUED,
    JobState.PRECHECKING,
    JobState.AWAITING_REVIEW,
    JobState.CHUNKING,
    JobState.EMBEDDING,
    JobState.INDEXING,
    JobState.COMPLETED,
]
"""Journal attendu d'un job mené à terme, événement d'ouverture compris."""


def set_decision(factory: SessionFactory, seeded: SeededJob, decision: PrecheckDecision) -> None:
    """Écrit l'aiguillage comme le ferait `PUT …/precheck/decision`."""

    async def _apply(db: AsyncSession) -> None:
        document = await db.get(Document, seeded.document_id)
        assert document is not None
        document.precheck_decision = decision.model_dump(mode="json")

    run_sync(factory, _apply)


def set_report(factory: SessionFactory, seeded: SeededJob, report: dict[str, object]) -> None:
    """Écrit le rapport de precheck comme le ferait `run_precheck` — champs fusionnés au défaut.

    Le document seedé par `make_document` porte déjà un `precheck_report` de
    base (`{"tables": 3, "scanned_pages": [2, 5]}`, sans `format` ni
    `is_likely_scanned`) : ce helper y superpose les champs fournis plutôt que
    de les remplacer, pour rester représentatif d'un rapport réellement écrit.
    """

    async def _apply(db: AsyncSession) -> None:
        document = await db.get(Document, seeded.document_id)
        assert document is not None
        document.precheck_report = {**(document.precheck_report or {}), **report}

    run_sync(factory, _apply)


def dataset_id_of(factory: SessionFactory, seeded: SeededJob) -> str | None:
    """Relit la référence du dataset persistée sur la base de connaissances."""

    async def _read(db: AsyncSession) -> str | None:
        document = await db.get(Document, seeded.document_id)
        assert document is not None
        knowledge_base = await db.get(KnowledgeBase, document.knowledge_base_id)
        assert knowledge_base is not None
        return knowledge_base.ragflow_dataset_external_id

    return run_sync(factory, _read)


# ─── Precheck ────────────────────────────────────────────────────────────────


def test_precheck_stops_on_the_human_decision(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_storage: FakeStorage,
) -> None:
    """`queued → prechecking → awaiting_review`, puis la tâche rend la main."""
    outcome = run_precheck(queued_job.argument)

    assert outcome == Outcome.EXECUTED
    assert state_of(worker_sessions, queued_job.job_id) is JobState.AWAITING_REVIEW
    assert [state for state, _ in journal_of(worker_sessions, queued_job.job_id)] == [
        JobState.QUEUED,
        JobState.PRECHECKING,
        JobState.AWAITING_REVIEW,
    ]
    assert (
        document_status_of(worker_sessions, queued_job.document_id)
        is DocumentStatus.AWAITING_REVIEW
    )


def test_precheck_reads_the_file_from_the_storage_port(
    worker_sessions: SessionFactory, queued_job: SeededJob, fake_storage: FakeStorage
) -> None:
    """Les octets viennent du port, jamais d'un état en mémoire du process d'upload."""
    document = document_of(worker_sessions, queued_job.document_id)

    run_precheck(queued_job.argument)

    assert fake_storage.reads == [document.storage_uri]


def test_precheck_persists_its_report_on_the_document(
    worker_sessions: SessionFactory, queued_job: SeededJob, fake_storage: FakeStorage
) -> None:
    """Le rapport alimente l'écran d'aiguillage : il doit être en base, pas en mémoire."""
    run_precheck(queued_job.argument)

    document = document_of(worker_sessions, queued_job.document_id)
    assert document.precheck_report is not None
    assert document.precheck_report["format"] == "pdf"
    assert document.precheck_report["supported"] is True
    assert document.page_count == 1


def test_precheck_persists_the_detected_languages_in_their_own_column(
    worker_sessions: SessionFactory, queued_job: SeededJob, fake_storage: FakeStorage
) -> None:
    """ADR-003 : les langues détectées ont une colonne dédiée, pas seulement le rapport."""
    run_precheck(queued_job.argument)

    document = document_of(worker_sessions, queued_job.document_id)
    assert document.precheck_report is not None
    assert document.detected_languages == ["fr"]
    assert document.detected_languages == document.precheck_report["detected_languages"]


def test_precheck_never_fails_on_an_unreadable_document(
    worker_sessions: SessionFactory, queued_job: SeededJob, fake_storage: FakeStorage
) -> None:
    """Un rapport dégradé n'empêche pas l'utilisateur de décider — il l'informe."""
    fake_storage.default = b"ceci n'est pas un PDF"

    outcome = run_precheck(queued_job.argument)

    assert outcome == Outcome.EXECUTED
    assert state_of(worker_sessions, queued_job.job_id) is JobState.AWAITING_REVIEW
    document = document_of(worker_sessions, queued_job.document_id)
    assert document.precheck_report is not None
    assert document.precheck_report["error"] is not None


def test_a_storage_failure_fails_the_job(
    worker_sessions: SessionFactory, queued_job: SeededJob, fake_storage: FakeStorage
) -> None:
    """Sans octets il n'y a rien à ingérer : c'est bien un échec, pas un rapport dégradé."""
    fake_storage.failure = StorageError("Volume de documents injoignable.")

    with pytest.raises(StorageError, match="injoignable"):
        run_precheck(queued_job.argument)

    assert state_of(worker_sessions, queued_job.job_id) is JobState.FAILED
    assert document_status_of(worker_sessions, queued_job.document_id) is DocumentStatus.FAILED
    job = job_of(worker_sessions, queued_job.job_id)
    assert job.error is not None
    assert job.error["retryable"] is False
    assert job.error["retry_state"] == JobState.QUEUED.value


# ─── Chunking : l'unique engagement du moteur ────────────────────────────────


def test_chunking_creates_the_dataset_and_persists_its_reference(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Le dataset est créé une fois et sa référence reste en base (ADR-012, décision 3)."""
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)

    run_chunking(queued_job.argument)

    assert fake_engine.ensured == [None]
    assert dataset_id_of(worker_sessions, queued_job) == DATASET_ID


def test_chunking_reuses_a_dataset_already_known(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Une base déjà déclarée au moteur ne doit jamais produire un second dataset."""
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)
    link_engine_refs(worker_sessions, queued_job)

    run_chunking(queued_job.argument)

    assert fake_engine.ensured == [EngineDatasetRef(external_id=DATASET_ID)]


async def _seed_two_documents_sharing_one_base(
    db: AsyncSession,
) -> tuple[uuid.UUID, SeededJob, SeededJob]:
    """Un propriétaire, une base neuve (sans dataset), deux documents à elle."""
    owner = make_user()
    db.add(owner)
    await db.flush([owner])

    knowledge_base = make_knowledge_base(owner, slug=f"chunking-race-{uuid.uuid4().hex[:8]}")
    db.add(knowledge_base)
    await db.flush()

    seeded: list[SeededJob] = []
    for _ in range(2):
        document = make_document(knowledge_base, filename=f"{uuid.uuid4().hex[:8]}.pdf")
        db.add(document)
        await db.flush([document])
        job = await IngestionJobService(db).open_for(document)
        seeded.append(SeededJob(job_id=job.id, document_id=document.id, owner_id=owner.id))

    return owner.id, seeded[0], seeded[1]


def test_concurrent_chunking_on_a_new_base_creates_only_one_dataset(
    worker_sessions: SessionFactory,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression : deux chunkings concurrents sur une base neuve ne doivent créer
    qu'un seul dataset côté moteur.

    Trouvé en ingérant un lot de documents en parallèle contre l'instance RAGFlow
    réelle (campagne de vérification E2E) : sans coordination, deux tâches
    `run_chunking` lisent toutes les deux `KnowledgeBase.ragflow_dataset_
    external_id is None` avant que l'une des deux ne commite la sienne — RAGFlow
    ne déduplique jamais par nom (ADR-012, décision 3), chacune crée donc son
    propre dataset. Un seul id gagne l'écriture en base ; les documents de la
    tâche perdante restent téléversés dans un dataset devenu orphelin, et leur
    embedding échoue ensuite avec `EngineError: You don't own the document`.

    `FakeIngestionEngine.ensure_dataset` rend toujours le même `DATASET_ID` pour
    `ref=None` (il ne modélise pas la création d'un *nouveau* dataset à chaque
    appel, contrairement à RAGFlow réel) : `fake_engine.ensured` reste
    l'observable exact de la régression malgré tout, puisque c'est le **nombre**
    d'appels avec `ref=None` — pas leur résultat — qui trahit l'absence de
    coordination entre les deux tâches.

    Deux threads réels (`ThreadPoolExecutor`), pas `asyncio.gather` : chaque
    `run_chunking` ouvre sa propre boucle d'événements (`asyncio.run`, cf.
    `app/workers/session.py`) et ne peut donc pas être attendu depuis celle du
    test — exactement le montage déjà utilisé par `tests/api/v1/
    test_pipeline_dispatch.py` pour lire depuis une connexion tierce.
    """
    owner_id, first, second = run_sync(worker_sessions, _seed_two_documents_sharing_one_base)
    try:
        advance_to(worker_sessions, first.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)
        advance_to(worker_sessions, second.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)

        # Sans ce rendez-vous, l'une des deux tâches peut traverser tout
        # `_chunk_document` (base locale, `FakeIngestionEngine` sans latence) avant que
        # l'autre n'ait même commencé à s'exécuter dans son propre thread —
        # la fenêtre de course ne serait alors jamais exercée, et ce test
        # passerait même sans le correctif. La barrière force les deux tâches
        # à charger la base de connaissances avant que l'une des deux ne
        # puisse lire `ragflow_dataset_external_id`, garantissant que les
        # deux l'observent à `None` (comportement pré-correctif) et que
        # seul le verrou (`pg_advisory_xact_lock`) arbitre laquelle crée le
        # dataset.
        barrier = threading.Barrier(2, timeout=10)
        original_knowledge_base_of = ingestion._knowledge_base_of

        async def _synced_knowledge_base_of(
            db: AsyncSession, job: ingestion.IngestionJob
        ) -> KnowledgeBase:
            knowledge_base = await original_knowledge_base_of(db, job)
            barrier.wait()
            return knowledge_base

        monkeypatch.setattr(ingestion, "_knowledge_base_of", _synced_knowledge_base_of)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(run_chunking, first.argument),
                pool.submit(run_chunking, second.argument),
            ]
            for future in futures:
                future.result(timeout=30)

        assert fake_engine.ensured.count(None) == 1
        assert state_of(worker_sessions, first.job_id) is JobState.CHUNKING
        assert state_of(worker_sessions, second.job_id) is JobState.CHUNKING
        assert dataset_id_of(worker_sessions, first) == dataset_id_of(worker_sessions, second)
    finally:

        async def _purge_owner(db: AsyncSession) -> None:
            await db.execute(delete(User).where(User.id == owner_id))

        run_sync(worker_sessions, _purge_owner)


def test_chunking_uploads_the_document_and_persists_its_reference(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Sans cette référence, les deux étapes de sondage n'auraient rien à interroger."""
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)
    document = document_of(worker_sessions, queued_job.document_id)

    run_chunking(queued_job.argument)

    assert fake_engine.uploads == [(document.filename, fake_storage.default)]
    reloaded = document_of(worker_sessions, queued_job.document_id)
    assert reloaded.ragflow_document_external_id == DOCUMENT_ID


def test_a_resumed_chunking_does_not_upload_the_document_twice(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Worker tué après le téléversement : la reprise réutilise le document du moteur."""
    advance_to(
        worker_sessions,
        queued_job.job_id,
        JobState.PRECHECKING,
        JobState.AWAITING_REVIEW,
        JobState.CHUNKING,
    )
    link_engine_refs(worker_sessions, queued_job)

    outcome = run_chunking(queued_job.argument)

    assert outcome == Outcome.RESUMED
    assert fake_engine.uploads == []
    assert len(fake_engine.started) == 1


def test_chunking_applies_the_default_profile_without_a_decision(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Un job repris sans aiguillage utilise le profil neutre, jamais une erreur.

    Le rapport de precheck seedé par défaut (`tests/db/factories.py::make_document`)
    ne porte ni `format` ni `is_likely_scanned` — cas volontairement dégradé.
    `is_likely_scanned_of` retombe alors sur son défaut prudent (`True`,
    `app/workers/tasks/ingestion.py`) : `DeepDOC`, pas `Plain Text`, exactement
    l'ancien comportement universel. Le cas nominal (rapport complet, document
    à couche texte propre) est couvert par
    `test_chunking_prefers_plain_text_on_a_clean_pdf_from_the_precheck_report`.
    """
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)

    run_chunking(queued_job.argument)

    assert [profile.chunk_method for profile in fake_engine.started] == [DEFAULT_CHUNK_METHOD]
    assert fake_engine.started[0].parser_config == {
        LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE
    }


def test_chunking_prefers_plain_text_on_a_clean_pdf_from_the_precheck_report(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Le nouveau défaut (ADR-023 §3) traverse réellement le pipeline, pas seulement la fonction.

    Un rapport de precheck qui établit une couche texte propre doit se
    traduire, une fois `run_chunking` exécutée, par `layout_recognize = "Plain
    Text"` côté moteur — sans aucune option cochée par l'utilisateur.
    """
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)
    set_report(worker_sessions, queued_job, {"format": "pdf", "is_likely_scanned": False})

    run_chunking(queued_job.argument)

    profile = fake_engine.started[0]
    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: PLAIN_TEXT_LAYOUT_RECOGNIZE}


def test_chunking_forces_deep_doc_on_a_scanned_document_from_the_precheck_report(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Symétrique du cas propre : un scan détecté au precheck impose `DeepDOC`, explicitement."""
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)
    set_report(worker_sessions, queued_job, {"format": "pdf", "is_likely_scanned": True})

    run_chunking(queued_job.argument)

    profile = fake_engine.started[0]
    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


def test_chunking_translates_the_human_decision_into_a_profile(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """C'est ici que la décision de l'utilisateur atteint réellement le moteur.

    Le document par défaut du banc de test est un PDF (`FakeStorage.default`) :
    `optimize_for_tables` s'y traduit par un renforcement de la mise en page, pas
    par `chunk_method=table` — RAGFlow refuse ce template sur un PDF (fait vérifié
    en test de bout en bout), `test_chunking_profile.py` couvre le cas tableur.
    """
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)
    set_decision(
        worker_sessions,
        queued_job,
        PrecheckDecision(optimize_for_tables=True, heavy_ocr=True),
    )

    run_chunking(queued_job.argument)

    profile = fake_engine.started[0]
    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


def test_chunking_routes_a_columns_and_tables_pdf_to_docling(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """ADR-043 traverse réellement le pipeline, pas seulement la fonction pure.

    Les trois signaux du rapport de precheck (`two_column_page_count`,
    `table_count`, `page_count`) doivent atteindre `profile_for_decision`
    depuis la colonne JSONB — c'est exactement le point de couture qu'une
    fonction pure testée seule ne prouve pas.
    """
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)
    set_report(
        worker_sessions,
        queued_job,
        {
            "format": "pdf",
            "is_likely_scanned": False,
            "two_column_page_count": 3,
            "table_count": 16,
            "page_count": 40,
        },
    )
    set_decision(worker_sessions, queued_job, PrecheckDecision(heavy_ocr=True))

    run_chunking(queued_job.argument)

    profile = fake_engine.started[0]
    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {
        LAYOUT_RECOGNIZE_KEY: DOCLING_LAYOUT_RECOGNIZE,
        TASK_PAGE_SIZE_KEY: 41,
    }


def test_chunking_leaves_a_columns_only_pdf_on_deep_doc(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Le pendant de non-régression : sans tableau, rien ne change dans le pipeline."""
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)
    set_report(
        worker_sessions,
        queued_job,
        {
            "format": "pdf",
            "is_likely_scanned": False,
            "two_column_page_count": 3,
            "table_count": 0,
            "page_count": 40,
        },
    )
    set_decision(worker_sessions, queued_job, PrecheckDecision(heavy_ocr=True))

    run_chunking(queued_job.argument)

    assert fake_engine.started[0].parser_config == {
        LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE
    }


def test_chunking_hands_over_to_the_polling_step(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    publications: Publications,
) -> None:
    """Le découpage ne conclut pas : il laisse le sondage constater la suite."""
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING, JobState.AWAITING_REVIEW)

    run_chunking(queued_job.argument)

    assert publications.successors == [ingestion.EMBEDDING_STEP.name]
    assert state_of(worker_sessions, queued_job.job_id) is JobState.CHUNKING


# ─── Traversée complète ──────────────────────────────────────────────────────


def test_a_job_walks_the_whole_state_machine(
    eager_celery: None,
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """Traversée complète, chaînage compris : `queued` … `completed`.

    `run_chunking` est invoquée explicitement — elle simule la décision humaine
    prise sur `awaiting_review` ; le reste du pipeline s'enchaîne tout seul, le
    moteur factice rendant un document déjà terminé.
    """
    run_precheck.delay(queued_job.argument)
    assert state_of(worker_sessions, queued_job.job_id) is JobState.AWAITING_REVIEW

    run_chunking.delay(queued_job.argument)

    assert state_of(worker_sessions, queued_job.job_id) is JobState.COMPLETED
    assert [state for state, _ in journal_of(worker_sessions, queued_job.job_id)] == NOMINAL_PATH
    assert document_status_of(worker_sessions, queued_job.document_id) is DocumentStatus.INDEXED


def test_the_final_job_is_coherent_with_the_read_endpoint(
    eager_celery: None,
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """L'état final est sérialisable tel quel par `GET /api/v1/jobs/{job_id}`."""
    run_precheck.delay(queued_job.argument)
    run_chunking.delay(queued_job.argument)

    payload = IngestionJobRead.model_validate(job_of(worker_sessions, queued_job.job_id))

    assert payload.state is JobState.COMPLETED
    assert payload.started_at is not None
    assert payload.finished_at is not None
    assert payload.error is None
    # `progress` du moteur est un flottant bruité dans [-1, 1], pas un
    # pourcentage (ADR-012) : la jauge n'invente rien, la frise se lit sur les états.
    assert payload.progress_percent == 0


def test_every_step_journals_a_readable_message(
    eager_celery: None,
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
) -> None:
    """La frise de progression de l'interface a besoin d'un libellé, pas d'un code."""
    run_precheck.delay(queued_job.argument)
    run_chunking.delay(queued_job.argument)

    messages = [message for _, message in journal_of(worker_sessions, queued_job.job_id)]

    assert all(message for message in messages)


# ─── Idempotence ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task",
    [run_precheck, run_chunking, run_embedding, run_indexing],
    ids=["precheck", "chunking", "embedding", "indexing"],
)
def test_a_task_replayed_on_a_completed_job_is_a_no_op(
    eager_celery: None,
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    fake_storage: FakeStorage,
    task: Task[[str], str],
) -> None:
    """Rejeu après redistribution : aucune exception, aucun état écrit."""
    run_precheck.delay(queued_job.argument)
    run_chunking.delay(queued_job.argument)
    before = journal_of(worker_sessions, queued_job.job_id)

    outcomes = [task(queued_job.argument) for _ in range(2)]

    assert outcomes == [Outcome.SKIPPED, Outcome.SKIPPED]
    assert journal_of(worker_sessions, queued_job.job_id) == before
    assert state_of(worker_sessions, queued_job.job_id) is JobState.COMPLETED


def test_a_task_replayed_beyond_its_source_state_writes_no_duplicate_event(
    worker_sessions: SessionFactory, queued_job: SeededJob, fake_storage: FakeStorage
) -> None:
    """Le job a dépassé `prechecking` : la tâche s'arrête, le journal ne bouge pas."""
    run_precheck(queued_job.argument)
    before = journal_of(worker_sessions, queued_job.job_id)

    assert run_precheck(queued_job.argument) == Outcome.SKIPPED
    assert run_precheck(queued_job.argument) == Outcome.SKIPPED
    assert journal_of(worker_sessions, queued_job.job_id) == before


def test_a_cancelled_job_stops_the_pipeline(
    worker_sessions: SessionFactory, queued_job: SeededJob, fake_storage: FakeStorage
) -> None:
    """Une annulation utilisateur est respectée par la tâche déjà en file."""
    advance_to(worker_sessions, queued_job.job_id, JobState.CANCELLED)

    assert run_precheck(queued_job.argument) == Outcome.SKIPPED
    assert state_of(worker_sessions, queued_job.job_id) is JobState.CANCELLED


def test_an_interrupted_step_is_resumed_without_replaying_its_transition(
    worker_sessions: SessionFactory, queued_job: SeededJob, fake_storage: FakeStorage
) -> None:
    """Worker tué après la transition d'entrée : la reprise finit l'étape, sans doublon."""
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING)

    outcome = run_precheck(queued_job.argument)

    assert outcome == Outcome.RESUMED
    assert state_of(worker_sessions, queued_job.job_id) is JobState.AWAITING_REVIEW
    states = [state for state, _ in journal_of(worker_sessions, queued_job.job_id)]
    assert states.count(JobState.PRECHECKING) == 1


# ─── Échecs ──────────────────────────────────────────────────────────────────


async def _explode(db: AsyncSession, job_id: uuid.UUID) -> None:
    """Corps d'étape qui échoue — même signature que `_StepBody`."""
    raise RuntimeError("service d'inférence injoignable")


def test_a_failing_step_marks_the_job_failed_and_reraises(
    worker_sessions: SessionFactory, queued_job: SeededJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les deux à la fois : le job reflète l'échec, l'exception remonte à Celery."""
    monkeypatch.setattr(ingestion, "_precheck_document", _explode)

    with pytest.raises(RuntimeError, match="injoignable"):
        run_precheck(queued_job.argument)

    assert state_of(worker_sessions, queued_job.job_id) is JobState.FAILED
    assert document_status_of(worker_sessions, queued_job.document_id) is DocumentStatus.FAILED


def test_a_failing_step_populates_the_structured_error(
    worker_sessions: SessionFactory, queued_job: SeededJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`IngestionJob.error` porte désormais une cause exploitable, pas seulement le journal.

    Une exception de programmation générique (`RuntimeError`) n'est ni une
    panne moteur déclarée ni retenue comme reprenable automatiquement : c'est
    la classe par défaut, la plus prudente (`_error_code_and_retryable`).
    """
    monkeypatch.setattr(ingestion, "_precheck_document", _explode)

    with pytest.raises(RuntimeError):
        run_precheck(queued_job.argument)

    job = job_of(worker_sessions, queued_job.job_id)
    assert job.error is not None
    assert job.error["code"] == "ingestion_step_failed"
    assert job.error["retryable"] is False
    assert job.error["retry_state"] == JobState.QUEUED.value
    assert "injoignable" in job.error["message"]


@pytest.mark.parametrize(
    ("build_error", "expected_code", "expected_retryable"),
    [
        (
            lambda: EngineTransientError("moteur injoignable"),
            "engine_transient",
            True,
        ),
        (
            lambda: EngineValidationError("chunk_method invalide"),
            "engine_validation",
            False,
        ),
        (lambda: EngineError("échec ambigu (code=102)"), "engine_error", False),
    ],
    ids=["engine_transient", "engine_validation", "engine_error"],
)
def test_the_error_classification_matches_the_engine_exception_hierarchy(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    monkeypatch: pytest.MonkeyPatch,
    build_error: Callable[[], EngineError],
    expected_code: str,
    expected_retryable: bool,
) -> None:
    """Seule `EngineTransientError` produit `retryable=True` (ADR-020).

    C'est cette distinction, et elle seule, qui doit nourrir `retry` — une
    panne passagère du moteur (réseau, RAGFlow injoignable) mérite une reprise
    automatique offerte ; une requête que le moteur a explicitement rejetée
    (`EngineValidationError`) ou un échec ambigu (`EngineError` générique) ne
    la méritent pas par défaut.
    """

    async def _explode_with(db: AsyncSession, job_id: uuid.UUID) -> None:
        raise build_error()

    monkeypatch.setattr(ingestion, "_precheck_document", _explode_with)

    with pytest.raises(EngineError):
        run_precheck(queued_job.argument)

    job = job_of(worker_sessions, queued_job.job_id)
    assert job.error is not None
    assert job.error["code"] == expected_code
    assert job.error["retryable"] is expected_retryable
    assert job.error["retry_state"] == JobState.QUEUED.value


def test_a_terminal_failure_logs_a_dead_letter(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Visibilité opérationnelle (ADR-020) : un échec réel produit un log marqué et unique."""
    monkeypatch.setattr(ingestion, "_precheck_document", _explode)

    with (
        caplog.at_level(logging.ERROR, logger="app.workers.tasks.ingestion"),
        pytest.raises(RuntimeError),
    ):
        run_precheck(queued_job.argument)

    dead_letters = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == DEAD_LETTER_MARKER
        and getattr(record, "job_id", None) == str(queued_job.job_id)
    ]
    assert len(dead_letters) == 1, "un seul repère de file morte par échec réel"


def test_the_failure_message_reaches_the_journal(
    worker_sessions: SessionFactory, queued_job: SeededJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le message doit être exploitable par l'utilisateur, pas un `500` opaque."""
    monkeypatch.setattr(ingestion, "_precheck_document", _explode)

    with pytest.raises(RuntimeError):
        run_precheck(queued_job.argument)

    state, message = journal_of(worker_sessions, queued_job.job_id)[-1]
    assert state is JobState.FAILED
    assert message is not None
    assert "injoignable" in message
    assert JobState.PRECHECKING.value in message


def test_a_failure_never_rewrites_a_terminal_job(
    worker_sessions: SessionFactory, queued_job: SeededJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un job devenu terminal pendant l'étape n'est pas ré-échoué : pas de doublon."""
    advance_to(worker_sessions, queued_job.job_id, JobState.PRECHECKING)

    async def _fail_then_explode(db: AsyncSession, job_id: uuid.UUID) -> None:
        """Échec concurrent, commité avant l'exception : le job est déjà terminal."""
        service = IngestionJobService(db)
        job = await service.get(job_id)
        await service.transition(job, JobState.FAILED, message="Interruption concurrente.")
        await db.commit()
        raise RuntimeError("interruption pendant l'étape")

    monkeypatch.setattr(ingestion, "_precheck_document", _fail_then_explode)

    with pytest.raises(RuntimeError, match="interruption"):
        run_precheck(queued_job.argument)

    states = [state for state, _ in journal_of(worker_sessions, queued_job.job_id)]
    assert state_of(worker_sessions, queued_job.job_id) is JobState.FAILED
    assert states.count(JobState.FAILED) == 1


def test_a_bounded_step_declares_no_engine_phase() -> None:
    """Precheck et chunking n'interrogent pas le moteur : rien à projeter chez eux."""
    assert ingestion.PRECHECK_STEP.engine_phases == frozenset()
    assert ingestion.CHUNKING_STEP.engine_phases == frozenset()
