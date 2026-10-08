"""Machine à états des jobs : le diagramme de `docs/domain-model.md` fait foi."""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.core.errors import ConflictError
from app.domain.enums import DocumentStatus, JobState
from app.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    CANCELLABLE_STATES,
    DOCUMENT_STATUS_BY_JOB_STATE,
    RETRY_SOURCE_STATES,
    TERMINAL_STATES,
    assert_transition,
    can_transition,
    document_status_for,
    is_terminal,
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

ILLEGAL_TRANSITIONS = [
    (JobState.QUEUED, JobState.COMPLETED),
    (JobState.QUEUED, JobState.CHUNKING),
    (JobState.PRECHECKING, JobState.CANCELLED),
    (JobState.CHUNKING, JobState.CANCELLED),
    (JobState.EMBEDDING, JobState.AWAITING_REVIEW),
    (JobState.INDEXING, JobState.QUEUED),
    (JobState.COMPLETED, JobState.FAILED),
    (JobState.FAILED, JobState.PRECHECKING),
    (JobState.FAILED, JobState.INDEXING),
    (JobState.FAILED, JobState.COMPLETED),
    (JobState.FAILED, JobState.CANCELLED),
    (JobState.CANCELLED, JobState.PRECHECKING),
]


def test_every_state_is_declared_in_the_transition_table() -> None:
    """Aucun état ne doit pouvoir être ajouté sans décider de ses successeurs."""
    assert set(ALLOWED_TRANSITIONS) == set(JobState)


@pytest.mark.parametrize(("source", "target"), list(pairwise(NOMINAL_PATH)))
def test_nominal_path_is_allowed(source: JobState, target: JobState) -> None:
    """Le chemin heureux traverse le pipeline d'un bout à l'autre."""
    assert can_transition(source, target)
    assert_transition(source, target)


@pytest.mark.parametrize("state", [s for s in JobState if s not in TERMINAL_STATES])
def test_any_non_terminal_state_can_fail(state: JobState) -> None:
    """« Tout état non terminal ─▶ failed » : un échec n'est jamais interdit."""
    assert can_transition(state, JobState.FAILED)


@pytest.mark.parametrize("state", sorted(CANCELLABLE_STATES))
def test_cancellable_states_accept_cancellation(state: JobState) -> None:
    """L'annulation n'est offerte que depuis `queued` et `awaiting_review`."""
    assert can_transition(state, JobState.CANCELLED)


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES - {JobState.FAILED}))
def test_terminal_states_have_no_successor(state: JobState) -> None:
    """`completed`/`cancelled` ne bougent plus, quelle que soit la cible demandée.

    `failed` est délibérément exclu : c'est la seule exception, couverte par
    `test_failed_is_terminal_but_manually_retryable` ci-dessous (ADR-020).
    """
    assert is_terminal(state)
    assert ALLOWED_TRANSITIONS[state] == frozenset()
    assert all(not can_transition(state, target) for target in JobState)


def test_failed_is_terminal_but_manually_retryable() -> None:
    """`failed` ne bouge jamais de lui-même, mais la reprise manuelle lui garde une sortie.

    `is_terminal` (consommé par la garde d'idempotence du pipeline,
    `app.workers.tasks.ingestion._fail`) reste vrai : rien dans le pipeline
    automatique ne relance un job en échec. `ALLOWED_TRANSITIONS[failed]`, en
    revanche, n'est plus vide depuis `POST /api/v1/jobs/{id}/retry` (ADR-020) —
    les deux notions ont divergé intentionnellement.
    """
    assert is_terminal(JobState.FAILED)
    assert ALLOWED_TRANSITIONS[JobState.FAILED] == RETRY_SOURCE_STATES
    for state in RETRY_SOURCE_STATES:
        assert can_transition(JobState.FAILED, state)
        assert_transition(JobState.FAILED, state)


@pytest.mark.parametrize("state", sorted(RETRY_SOURCE_STATES))
def test_retry_source_states_are_exactly_the_pipeline_step_sources(state: JobState) -> None:
    """Chaque cible de reprise est un `Step.source` réel du pipeline (§9 CONTRIBUTING.md).

    Un import différé évite un cycle (`app.workers.tasks.ingestion` importe
    `app.services.jobs`, qui reste indépendant de `app.workers`) : ce test relie
    quand même les deux tables pour qu'un futur ajout d'étape ne puisse pas les
    faire diverger en silence.
    """
    from app.workers.tasks.ingestion import PIPELINE_STEP_SOURCES

    assert state in PIPELINE_STEP_SOURCES


@pytest.mark.parametrize(("source", "target"), ILLEGAL_TRANSITIONS)
def test_illegal_transitions_raise_conflict(source: JobState, target: JobState) -> None:
    """Une transition illégale lève `ConflictError` → HTTP 409."""
    assert not can_transition(source, target)
    with pytest.raises(ConflictError) as excinfo:
        assert_transition(source, target)

    problem = excinfo.value.to_problem()
    assert problem.status == 409
    extensions = problem.model_dump()
    assert extensions["current_state"] == source.value
    assert extensions["requested_state"] == target.value


@pytest.mark.parametrize("state", list(JobState))
def test_no_state_transitions_to_itself(state: JobState) -> None:
    """Le sur-place est refusé : une transition doit toujours faire avancer le job."""
    assert not can_transition(state, state)


def test_document_status_projection_covers_every_state() -> None:
    """Chaque état de job projette un statut de document, sans trou."""
    assert set(DOCUMENT_STATUS_BY_JOB_STATE) == set(JobState)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (JobState.QUEUED, DocumentStatus.PENDING),
        (JobState.PRECHECKING, DocumentStatus.PROCESSING),
        (JobState.AWAITING_REVIEW, DocumentStatus.AWAITING_REVIEW),
        (JobState.INDEXING, DocumentStatus.PROCESSING),
        (JobState.COMPLETED, DocumentStatus.INDEXED),
        (JobState.FAILED, DocumentStatus.FAILED),
        (JobState.CANCELLED, DocumentStatus.CANCELLED),
    ],
)
def test_document_status_projection(state: JobState, expected: DocumentStatus) -> None:
    """Un job annulé laisse le document ré-ingérable, mais visiblement `cancelled` — pas
    confondu avec `pending`, qui laisserait croire à un traitement encore à venir."""
    assert document_status_for(state) is expected
