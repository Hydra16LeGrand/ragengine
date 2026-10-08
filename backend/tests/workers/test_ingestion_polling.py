"""Sondage du moteur : bascule conditionnelle, replanification, veille anti-blocage.

Ces deux étapes n'ont pas la forme des autres, et ces tests disent pourquoi elles
ne peuvent pas l'avoir. Le moteur d'ingestion n'expose qu'un seul état discret,
ne rappelle personne, et peut rester `running` indéfiniment sur un bug de parsing
(ADR-012, fait 4). Il faut donc que RAGEngine :

1. **n'écrive sa transition que lorsque la condition est vraie** — une bascule
   optimiste afficherait « vectorisation » sur un document que le moteur n'a même
   pas commencé à découper ;
2. **n'occupe aucun worker en attendant** — d'où la replanification, jamais un
   `sleep` ;
3. **sache s'extraire seul d'un blocage** — c'est la veille, et elle échoue le
   job (`failed`) plutôt que de l'annuler : un blocage n'est pas une décision de
   l'utilisateur ;
4. **ne fasse jamais tourner deux chaînes de sondage sur le même job** — d'où la
   garde qui arrête net une tâche redélivrée dont la bascule est déjà écrite.

Le moteur est un double scénarisé : c'est le seul moyen de reproduire à la
demande un parsing figé ou un échec, que l'instance réelle ne produit pas sur
commande.
"""

from __future__ import annotations

import logging
import uuid
from typing import Final

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Document
from app.domain.enums import DocumentStatus, JobState
from app.providers.engine.fake import FakeIngestionEngine
from app.workers.tasks import ingestion
from app.workers.tasks.ingestion import (
    DEAD_LETTER_MARKER,
    EMBEDDING_STEP,
    INDEXING_STEP,
    Outcome,
    run_embedding,
    run_indexing,
)
from tests.workers.conftest import (
    DONE,
    Publications,
    SeededJob,
    SessionFactory,
    advance_to,
    age_last_observation,
    document_status_of,
    job_of,
    journal_of,
    link_engine_refs,
    parse_status,
    run_sync,
    state_of,
)

CHUNKING_IN_PROGRESS = parse_status(
    "running", inferred_phase=JobState.CHUNKING, progress=0.2, last_message="Start to parse."
)
EMBEDDING_IN_PROGRESS = parse_status(
    "running", inferred_phase=JobState.EMBEDDING, progress=0.6, last_message="Embedding chunks."
)
INDEXING_IN_PROGRESS = parse_status(
    "running", inferred_phase=JobState.INDEXING, progress=0.9, last_message="Indexing done."
)
STALLED = parse_status("running", inferred_phase=None, progress=0.0, last_message=None)
"""Le blocage observé en audit : `running`, aucune progression, journal vide."""


@pytest.fixture
def chunking_job(worker_sessions: SessionFactory, queued_job: SeededJob) -> SeededJob:
    """Job arrivé en `chunking`, avec les références que le moteur lui connaît."""
    advance_to(
        worker_sessions,
        queued_job.job_id,
        JobState.PRECHECKING,
        JobState.AWAITING_REVIEW,
        JobState.CHUNKING,
    )
    link_engine_refs(worker_sessions, queued_job)
    return queued_job


@pytest.fixture
def embedding_job(worker_sessions: SessionFactory, chunking_job: SeededJob) -> SeededJob:
    """Job arrivé en `embedding` : la prochaine bascule est celle de l'indexation."""
    advance_to(worker_sessions, chunking_job.job_id, JobState.EMBEDDING)
    return chunking_job


# ─── Attente : rien n'est écrit tant que la condition n'est pas remplie ──────


def test_embedding_waits_while_the_engine_is_still_chunking(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Aucune transition, aucun événement : la tâche se contente de revenir plus tard."""
    fake_engine.statuses = [CHUNKING_IN_PROGRESS]
    before = journal_of(worker_sessions, chunking_job.job_id)

    outcome = run_embedding(chunking_job.argument)

    assert outcome == Outcome.WAITING
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.CHUNKING
    assert journal_of(worker_sessions, chunking_job.job_id) == before
    assert publications.reschedules == [EMBEDDING_STEP.name]
    assert publications.successors == []


def test_an_unrecognised_engine_message_never_guesses_a_phase(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Projection `None` (message imprévu) : on attend, on ne devine pas (ADR-012)."""
    fake_engine.statuses = [
        parse_status("running", inferred_phase=None, progress=0.3, last_message="Message inédit.")
    ]

    assert run_embedding(chunking_job.argument) == Outcome.WAITING
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.CHUNKING


def test_waiting_still_records_what_the_engine_reported(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Sans cette écriture, la veille n'aurait aucune origine à comparer."""
    fake_engine.statuses = [CHUNKING_IN_PROGRESS]

    run_embedding(chunking_job.argument)

    job = job_of(worker_sessions, chunking_job.job_id)
    assert job.engine_progress == CHUNKING_IN_PROGRESS.progress
    assert job.engine_last_message == CHUNKING_IN_PROGRESS.last_message
    assert job.engine_progress_observed_at is not None


# ─── Bascule : la transition n'est écrite qu'une fois la condition vraie ─────


def test_embedding_transitions_once_the_engine_reaches_the_phase(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """`chunking → embedding`, puis la main passe — pas de replanification de soi-même."""
    fake_engine.statuses = [EMBEDDING_IN_PROGRESS]

    outcome = run_embedding(chunking_job.argument)

    assert outcome == Outcome.EXECUTED
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.EMBEDDING
    assert publications.successors == [INDEXING_STEP.name]
    assert publications.reschedules == []


def test_the_engine_message_is_journalled_with_the_transition(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """La frise de progression montre ce que le moteur a réellement dit."""
    fake_engine.statuses = [EMBEDDING_IN_PROGRESS]

    run_embedding(chunking_job.argument)

    state, message = journal_of(worker_sessions, chunking_job.job_id)[-1]
    assert state is JobState.EMBEDDING
    assert message == EMBEDDING_IN_PROGRESS.last_message


def test_a_phase_already_overtaken_still_triggers_the_transition(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Le sondage peut arriver après deux marqueurs : `indexing` vaut aussi `embedding`."""
    fake_engine.statuses = [INDEXING_IN_PROGRESS]

    assert run_embedding(chunking_job.argument) == Outcome.EXECUTED
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.EMBEDDING


def test_a_finished_document_triggers_the_transition_too(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """`done` tranche sans ambiguïté, quelle que soit la phase projetée."""
    fake_engine.statuses = [DONE]

    assert run_embedding(chunking_job.argument) == Outcome.EXECUTED
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.EMBEDDING
    assert publications.successors == [INDEXING_STEP.name]


# ─── Indexation : deux transitions possibles sur un même sondage ─────────────


def test_indexing_waits_while_the_engine_has_not_reached_the_phase(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Tant que le moteur vectorise, l'indexation n'a rien à écrire."""
    fake_engine.statuses = [EMBEDDING_IN_PROGRESS]

    assert run_indexing(embedding_job.argument) == Outcome.WAITING
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.EMBEDDING
    assert publications.reschedules == [INDEXING_STEP.name]


def test_indexing_transitions_then_keeps_polling_for_the_completion(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """`indexing` atteint mais pas fini : on écrit la bascule et on revient plus tard."""
    fake_engine.statuses = [INDEXING_IN_PROGRESS]

    outcome = run_indexing(embedding_job.argument)

    assert outcome == Outcome.EXECUTED
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.INDEXING
    assert publications.reschedules == [INDEXING_STEP.name]


def test_both_transitions_may_land_on_the_same_poll(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Un document qui termine vite : `embedding → indexing → completed` d'un coup.

    Ce n'est pas sauter un état : ce sont les deux transitions propres à cette
    étape, écrites l'une après l'autre dans le même sondage.
    """
    fake_engine.statuses = [DONE]

    outcome = run_indexing(embedding_job.argument)

    assert outcome == Outcome.EXECUTED
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.COMPLETED
    states = [state for state, _ in journal_of(worker_sessions, embedding_job.job_id)]
    assert states[-2:] == [JobState.INDEXING, JobState.COMPLETED]
    assert publications.reschedules == []


def test_indexing_concludes_when_the_engine_finishes_later(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Bascule écrite au premier sondage, conclusion au second : aucun doublon."""
    fake_engine.statuses = [INDEXING_IN_PROGRESS]
    run_indexing(embedding_job.argument)
    fake_engine.statuses = [DONE]

    outcome = run_indexing(embedding_job.argument)

    assert outcome == Outcome.RESUMED
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.COMPLETED
    states = [state for state, _ in journal_of(worker_sessions, embedding_job.job_id)]
    assert states.count(JobState.INDEXING) == 1
    assert document_status_of(worker_sessions, embedding_job.document_id) is DocumentStatus.INDEXED


def test_indexing_keeps_polling_after_its_transition_until_the_engine_finishes(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Entre la bascule et la conclusion, la tâche se replanifie sans rien écrire."""
    fake_engine.statuses = [INDEXING_IN_PROGRESS]
    run_indexing(embedding_job.argument)
    before = journal_of(worker_sessions, embedding_job.job_id)

    outcome = run_indexing(embedding_job.argument)

    assert outcome == Outcome.WAITING
    assert journal_of(worker_sessions, embedding_job.job_id) == before
    assert publications.reschedules == [INDEXING_STEP.name, INDEXING_STEP.name]


# ─── Idempotence : jamais deux chaînes de sondage sur le même job ────────────


def test_a_redelivered_embedding_stops_instead_of_starting_a_second_chain(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """La bascule est déjà écrite : une seconde chaîne sonderait le même document."""
    outcome = run_embedding(embedding_job.argument)

    assert outcome == Outcome.SKIPPED
    assert publications.reschedules == []
    assert publications.successors == []
    assert fake_engine.polls == 0


@pytest.mark.parametrize("terminal", [JobState.CANCELLED, JobState.FAILED])
def test_a_terminal_job_stops_the_polling(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
    terminal: JobState,
) -> None:
    """Annulation ou échec : la tâche déjà en file s'arrête sans rien interroger."""
    advance_to(worker_sessions, queued_job.job_id, terminal)

    assert run_embedding(queued_job.argument) == Outcome.SKIPPED
    assert fake_engine.polls == 0
    assert publications.reschedules == []


def test_a_completed_job_stops_the_indexing_polling(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Message tardif sur un job déjà conclu : terminal, aucune replanification."""
    fake_engine.statuses = [DONE]
    run_indexing(embedding_job.argument)

    assert run_indexing(embedding_job.argument) == Outcome.SKIPPED
    assert publications.reschedules == []


# ─── Échecs rapportés par le moteur ──────────────────────────────────────────


def test_an_engine_failure_fails_the_job_with_its_own_message(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """L'utilisateur doit lire la cause du moteur, pas un échec générique."""
    fake_engine.statuses = [
        parse_status("fail", progress=-1.0, last_message="Boom.", error="Fichier illisible.")
    ]

    outcome = run_embedding(chunking_job.argument)

    assert outcome == Outcome.EXECUTED
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.FAILED
    _, message = journal_of(worker_sessions, chunking_job.job_id)[-1]
    assert message == "Fichier illisible."
    assert publications.reschedules == []
    job = job_of(worker_sessions, chunking_job.job_id)
    assert job.error is not None
    assert job.error["code"] == "engine_processing_failed"
    assert job.error["retryable"] is False
    # `EMBEDDING_STEP.source` : reprendre republierait `run_embedding` depuis `chunking`.
    assert job.error["retry_state"] == JobState.CHUNKING.value


def test_an_engine_cancellation_stops_the_polling(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Interruption venue d'ailleurs : sans cette branche, on sonderait à l'infini."""
    fake_engine.statuses = [parse_status("cancel", progress=0.4, last_message="Cancelled.")]

    outcome = run_embedding(chunking_job.argument)

    assert outcome == Outcome.EXECUTED
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.FAILED
    assert publications.reschedules == []
    job = job_of(worker_sessions, chunking_job.job_id)
    assert job.error is not None
    assert job.error["code"] == "engine_processing_cancelled"
    assert job.error["retryable"] is False


def test_a_polling_failure_logs_a_dead_letter_too(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`_run_polling_step` a son propre point de dispatch, distinct de `_run_step` (ADR-020)."""
    fake_engine.statuses = [
        parse_status("fail", progress=-1.0, last_message="Boom.", error="Fichier illisible.")
    ]

    with caplog.at_level(logging.ERROR, logger="app.workers.tasks.ingestion"):
        run_embedding(chunking_job.argument)

    dead_letters = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == DEAD_LETTER_MARKER
        and getattr(record, "job_id", None) == str(chunking_job.job_id)
    ]
    assert len(dead_letters) == 1


# ─── Veille anti-blocage ─────────────────────────────────────────────────────


def test_a_stalled_document_is_cancelled_and_the_job_fails(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Le moteur ne s'en sortira pas seul : RAGEngine l'interrompt puis échoue le job."""
    fake_engine.statuses = [STALLED]
    run_embedding(chunking_job.argument)
    age_last_observation(
        worker_sessions,
        chunking_job.job_id,
        seconds=get_settings().ragflow_stuck_job_timeout_seconds + 60,
    )

    outcome = run_embedding(chunking_job.argument)

    assert outcome == Outcome.EXECUTED
    assert fake_engine.cancellations == 1
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.FAILED
    assert document_status_of(worker_sessions, chunking_job.document_id) is DocumentStatus.FAILED
    job = job_of(worker_sessions, chunking_job.job_id)
    assert job.error is not None
    assert job.error["code"] == "engine_stalled"
    assert job.error["retryable"] is False


def test_the_stall_message_names_the_elapsed_time(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """« Le moteur n'a plus progressé depuis Xs » : un message actionnable, pas un code."""
    fake_engine.statuses = [STALLED]
    run_embedding(chunking_job.argument)
    age_last_observation(
        worker_sessions,
        chunking_job.job_id,
        seconds=get_settings().ragflow_stuck_job_timeout_seconds + 60,
    )

    run_embedding(chunking_job.argument)

    _, message = journal_of(worker_sessions, chunking_job.job_id)[-1]
    assert message is not None
    assert "n'a plus progressé depuis" in message


def test_the_watchdog_never_fires_while_the_engine_still_moves(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Un gros document est lent, pas bloqué : chaque évolution réarme le délai."""
    fake_engine.statuses = [
        parse_status("running", inferred_phase=JobState.CHUNKING, progress=0.1),
        parse_status("running", inferred_phase=JobState.CHUNKING, progress=0.4),
    ]
    run_embedding(chunking_job.argument)
    age_last_observation(
        worker_sessions,
        chunking_job.job_id,
        seconds=get_settings().ragflow_stuck_job_timeout_seconds + 60,
    )

    outcome = run_embedding(chunking_job.argument)

    assert outcome == Outcome.WAITING
    assert fake_engine.cancellations == 0
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.CHUNKING


def test_a_changed_message_alone_counts_as_progress(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Le `progress` du moteur est bruité : le journal seul suffit à prouver la vie."""
    fake_engine.statuses = [
        parse_status("running", inferred_phase=JobState.CHUNKING, last_message="Page 1."),
        parse_status("running", inferred_phase=JobState.CHUNKING, last_message="Page 2."),
    ]
    run_embedding(chunking_job.argument)
    age_last_observation(
        worker_sessions,
        chunking_job.job_id,
        seconds=get_settings().ragflow_stuck_job_timeout_seconds + 60,
    )

    assert run_embedding(chunking_job.argument) == Outcome.WAITING
    assert fake_engine.cancellations == 0


def test_the_watchdog_also_guards_the_indexing_step(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Un blocage peut survenir après la bascule : la veille ne s'arrête pas là."""
    fake_engine.statuses = [INDEXING_IN_PROGRESS]
    run_indexing(embedding_job.argument)
    age_last_observation(
        worker_sessions,
        embedding_job.job_id,
        seconds=get_settings().ragflow_stuck_job_timeout_seconds + 60,
    )

    run_indexing(embedding_job.argument)

    assert fake_engine.cancellations == 1
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.FAILED


# ─── Références manquantes : un défaut d'étape, pas un aléa ─────────────────


def test_polling_without_engine_references_is_a_permanent_error(
    worker_sessions: SessionFactory,
    queued_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """`ValueError` : exclue du retry Celery, la rejouer donnerait le même manque."""
    advance_to(
        worker_sessions,
        queued_job.job_id,
        JobState.PRECHECKING,
        JobState.AWAITING_REVIEW,
        JobState.CHUNKING,
    )

    with pytest.raises(ValueError, match="moteur d'ingestion"):
        run_embedding(queued_job.argument)

    assert state_of(worker_sessions, queued_job.job_id) is JobState.FAILED
    job = job_of(worker_sessions, queued_job.job_id)
    assert job.error is not None
    assert job.error["retryable"] is False
    # `EMBEDDING_STEP.source` : la reprise republierait `run_embedding` sur le
    # même manque de références — non retryable, mais `retry_state` reste
    # renseigné pour que `force=true` reste possible côté opérateur.
    assert job.error["retry_state"] == JobState.CHUNKING.value


# ─── Replanification : la seule forme d'attente autorisée ───────────────────


def test_rescheduling_republishes_the_same_task_with_the_configured_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aucun `sleep` : le worker est rendu à la file entre deux sondages (ADR-013).

    Ce test regarde la publication réelle, que les autres interceptent : c'est le
    seul endroit qui vérifie que la tâche republiée est bien **elle-même**, et
    non son successeur, et qu'elle porte le délai configuré.
    """
    published: list[tuple[str, tuple[object, ...], object]] = []

    class _Recorder:
        """Substitut de tâche Celery, qui note au lieu de publier."""

        def __init__(self, name: str) -> None:
            self.name = name

        def apply_async(self, args: tuple[object, ...] | None = None, **options: object) -> object:
            published.append((self.name, args or (), options.get("countdown")))
            return None

    monkeypatch.setattr(ingestion, "task_by_name", lambda name: _Recorder(name))
    job_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    ingestion._reschedule(EMBEDDING_STEP, job_id)

    assert published == [
        (EMBEDDING_STEP.name, (str(job_id),), get_settings().ragflow_poll_interval_seconds)
    ]


# ─── ADR-043 — un job Docling n'est jamais conclu sur un index vide ──────────
#
# Le moteur annonce `run == "done"` **avant** d'avoir écrit l'index sur ce
# profil-là : `DoclingParser.parse_pdf()` termine par `callback(1.0, "[Docling]
# Done.")` à la fin du parsing, donc avant le découpage, l'embedding et
# l'écriture ES (ADR-043, défaut n°2 — 7 s d'écart mesurées sur deux pages, et
# une passe rendue `done` avec zéro fragment). Le critère de fin est donc
# `chunk_count`, écrit après l'insertion et jamais avant.


DOCLING_REPORT: Final = {
    "format": "pdf",
    "is_likely_scanned": False,
    "two_column_page_count": 3,
    "table_count": 16,
    "page_count": 40,
}
"""Signaux de precheck qui routent vers Docling — ceux de `real-notice-2042.pdf`."""

DONE_WITHOUT_INDEX: Final = parse_status(
    "done",
    inferred_phase=JobState.COMPLETED,
    progress=1.0,
    last_message="[Docling] Done.",
    chunk_count=0,
)
"""Ce que le moteur rend pendant la fenêtre : terminé, et rien d'indexé."""

DONE_WITH_INDEX: Final = parse_status(
    "done",
    inferred_phase=JobState.COMPLETED,
    progress=1.0,
    last_message="[Docling] Done.",
    chunk_count=149,
)
"""Le même état, une fois `increment_chunk_num` passé côté moteur."""


def _route_to_docling(factory: SessionFactory, seeded: SeededJob) -> None:
    """Écrit le rapport et la décision qui font router le document vers Docling."""

    async def _apply(db: AsyncSession) -> None:
        document = await db.get(Document, seeded.document_id)
        assert document is not None
        document.precheck_report = {**(document.precheck_report or {}), **DOCLING_REPORT}
        document.precheck_decision = {"heavy_ocr": True}

    run_sync(factory, _apply)


def test_a_docling_job_is_not_completed_while_the_index_is_still_empty(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Le défaut, pris sur le fait : `run == "done"` avec zéro fragment ne conclut rien.

    C'est le test rouge avant le correctif — sans lui, le job partait en
    `completed` sur un index vide, et l'utilisateur voyait un document
    « indexé » que la recherche ne retrouvait pas.
    """
    _route_to_docling(worker_sessions, embedding_job)
    fake_engine.statuses = [DONE_WITHOUT_INDEX]

    outcome = run_indexing(embedding_job.argument)

    assert outcome == Outcome.WAITING
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.EMBEDDING
    assert publications.reschedules == [INDEXING_STEP.name]


def test_a_docling_job_completes_as_soon_as_its_fragments_exist(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """L'attente n'est pas un blocage : le premier fragment écrit conclut le job."""
    _route_to_docling(worker_sessions, embedding_job)
    fake_engine.statuses = [DONE_WITHOUT_INDEX, DONE_WITHOUT_INDEX, DONE_WITH_INDEX]

    assert run_indexing(embedding_job.argument) == Outcome.WAITING
    assert run_indexing(embedding_job.argument) == Outcome.WAITING
    assert run_indexing(embedding_job.argument) == Outcome.EXECUTED

    assert state_of(worker_sessions, embedding_job.job_id) is JobState.COMPLETED
    assert document_status_of(worker_sessions, embedding_job.document_id) is DocumentStatus.INDEXED


def test_the_docling_guard_also_holds_the_embedding_step(
    worker_sessions: SessionFactory,
    chunking_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Les deux étapes de sondage partagent le même critère de fin, donc la même garde.

    Sans cela, `run_embedding` conclurait sa bascule sur le `done` prématuré et
    passerait la main à `run_indexing`, qui trouverait un job déjà avancé : la
    garde ne tiendrait qu'à un cheveu de séquencement.
    """
    _route_to_docling(worker_sessions, chunking_job)
    fake_engine.statuses = [DONE_WITHOUT_INDEX]

    assert run_embedding(chunking_job.argument) == Outcome.WAITING
    assert state_of(worker_sessions, chunking_job.job_id) is JobState.CHUNKING
    assert publications.successors == []


def test_the_guard_never_delays_a_deep_doc_job(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Non-régression du profil `DeepDOC` : `done` conclut, avec ou sans compte.

    `chunk_count` reste à 0 dans cet état — la garde ne le regarde pas hors
    Docling, et c'est exactement ce que ce test verrouille : le correctif est
    additif, il ne change le critère de fin d'aucun autre profil.
    """

    async def _scanned(db: AsyncSession) -> None:
        document = await db.get(Document, embedding_job.document_id)
        assert document is not None
        document.precheck_report = {"format": "pdf", "is_likely_scanned": True}

    run_sync(worker_sessions, _scanned)
    fake_engine.statuses = [DONE_WITHOUT_INDEX]

    assert run_indexing(embedding_job.argument) == Outcome.EXECUTED
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.COMPLETED


def test_the_guard_never_delays_a_plain_text_job(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """Même non-régression sur le profil par défaut du contrat d'API v1."""

    async def _clean(db: AsyncSession) -> None:
        document = await db.get(Document, embedding_job.document_id)
        assert document is not None
        document.precheck_report = {"format": "pdf", "is_likely_scanned": False}
        document.precheck_decision = {}

    run_sync(worker_sessions, _clean)
    fake_engine.statuses = [DONE_WITHOUT_INDEX]

    assert run_indexing(embedding_job.argument) == Outcome.EXECUTED
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.COMPLETED


def test_an_endless_docling_wait_is_still_caught_by_the_stall_watchdog(
    worker_sessions: SessionFactory,
    embedding_job: SeededJob,
    fake_engine: FakeIngestionEngine,
    publications: Publications,
) -> None:
    """L'attente a une fin : un Docling qui n'écrirait jamais rien échoue, il ne tourne pas.

    Le rapprochement de progression du moteur cesse de toucher un document déjà
    `DONE` : `progress_msg` se fige, la veille anti-blocage accumule son
    immobilité et tranche. C'est ce qui rend l'attente sûre — jamais un job
    orphelin, jamais un `completed` mensonger.
    """
    _route_to_docling(worker_sessions, embedding_job)
    fake_engine.statuses = [DONE_WITHOUT_INDEX]

    assert run_indexing(embedding_job.argument) == Outcome.WAITING
    age_last_observation(
        worker_sessions,
        embedding_job.job_id,
        seconds=get_settings().ragflow_stuck_job_timeout_seconds + 1,
    )

    assert run_indexing(embedding_job.argument) == Outcome.EXECUTED
    assert state_of(worker_sessions, embedding_job.job_id) is JobState.FAILED
    assert fake_engine.cancellations == 1
