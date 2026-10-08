"""Machine à états des jobs d'ingestion — logique **pure**, sans aucune I/O.

Ce module est la seule définition des transitions légales. Il est consommé par
le service de transition (Phase 1) et par les tâches Celery (Phase 3) : il ne
dépend donc ni de SQLAlchemy, ni de FastAPI, ni d'un SDK externe — uniquement
des énumérations du domaine et du contrat d'erreur.

Diagramme de référence (`docs/domain-model.md` §3) :

    queued ─▶ prechecking ─▶ awaiting_review ─▶ chunking ─▶ embedding ─▶ indexing ─▶ completed
    tout état non terminal ─▶ failed
    queued | awaiting_review ─▶ cancelled
    failed ─▶ queued | awaiting_review | chunking | embedding  (reprise manuelle, ADR-020)

La dernière arête est la seule exception à « `failed` est terminal » : elle
n'existe que pour `POST /api/v1/jobs/{id}/retry` (`IngestionJobService.retry`),
jamais empruntée automatiquement par le pipeline lui-même — `is_terminal(failed)`
reste vrai, rien ne relance un job en échec de son propre chef.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue

from app.core.errors import ConflictError
from app.domain.enums import DocumentStatus, JobState

TERMINAL_STATES: Final[frozenset[JobState]] = frozenset(
    {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
)
"""États que le pipeline **automatique** ne quitte jamais de lui-même
(`is_terminal`, tête de module). `completed` et `cancelled` n'ont aucun
successeur, point final. `failed` reste dans cet ensemble pour ce sens précis —
mais `ALLOWED_TRANSITIONS[failed]` n'est plus vide depuis la reprise manuelle
(`RETRY_SOURCE_STATES` ci-dessous) : les deux notions ont divergé le jour où
`POST /jobs/{id}/retry` a existé, c'est assumé, pas un oubli."""

CANCELLABLE_STATES: Final[frozenset[JobState]] = frozenset(
    {JobState.QUEUED, JobState.AWAITING_REVIEW}
)
"""États depuis lesquels l'annulation utilisateur est acceptée."""

RETRY_SOURCE_STATES: Final[frozenset[JobState]] = frozenset(
    {JobState.QUEUED, JobState.AWAITING_REVIEW, JobState.CHUNKING, JobState.EMBEDDING}
)
"""Cibles légales d'une reprise manuelle depuis `failed` (ADR-020).

Exactement les quatre `Step.source` du pipeline (`app.workers.tasks.ingestion` :
`PRECHECK_STEP`, `CHUNKING_STEP`, `EMBEDDING_STEP`, `INDEXING_STEP`) — reprendre
un job replace son état à celui **attendu en entrée** de l'étape qui a échoué,
jamais à l'état que cette étape écrit elle-même (`Step.target`), qui reste
réservé à la garde d'idempotence de la tâche Celery. `app.workers.celery_app.
RETRY_TASK_BY_SOURCE_STATE` fait le lien inverse : quelle tâche republier pour
chacun de ces états."""

_NOMINAL_PATH: Final[Mapping[JobState, JobState]] = {
    JobState.QUEUED: JobState.PRECHECKING,
    JobState.PRECHECKING: JobState.AWAITING_REVIEW,
    # Le timeout de décision emprunte la même arête que la décision explicite
    # (ADR-055) : `app.workers.tasks.maintenance.resume_expired_precheck_decision`
    # republie `run_chunking` sans écrire cette transition elle-même — c'est
    # `_enter` (`app.workers.tasks.ingestion`) qui l'écrit à l'exécution, exactement
    # comme pour une décision humaine explicite (`PUT .../precheck/decision`).
    JobState.AWAITING_REVIEW: JobState.CHUNKING,
    JobState.CHUNKING: JobState.EMBEDDING,
    JobState.EMBEDDING: JobState.INDEXING,
    JobState.INDEXING: JobState.COMPLETED,
}


def _build_allowed_transitions() -> Mapping[JobState, frozenset[JobState]]:
    """Assemble la table des transitions à partir du chemin nominal et des sorties."""
    transitions: dict[JobState, frozenset[JobState]] = {}
    for state in JobState:
        if state is JobState.FAILED:
            # Seule exception à « terminal = sans successeur » : la reprise
            # manuelle (ADR-020). `completed`/`cancelled` restent, eux, sans
            # aucune arête sortante — seul un échec se répare.
            transitions[state] = RETRY_SOURCE_STATES
            continue
        if state in TERMINAL_STATES:
            transitions[state] = frozenset()
            continue
        successors = {JobState.FAILED}
        nominal = _NOMINAL_PATH.get(state)
        if nominal is not None:
            successors.add(nominal)
        if state in CANCELLABLE_STATES:
            successors.add(JobState.CANCELLED)
        transitions[state] = frozenset(successors)
    return transitions


ALLOWED_TRANSITIONS: Final[Mapping[JobState, frozenset[JobState]]] = _build_allowed_transitions()
"""Successeurs légaux de chaque état. Un état absent de ses successeurs interdit le sur-place."""

DOCUMENT_STATUS_BY_JOB_STATE: Final[Mapping[JobState, DocumentStatus]] = {
    JobState.QUEUED: DocumentStatus.PENDING,
    JobState.PRECHECKING: DocumentStatus.PROCESSING,
    JobState.AWAITING_REVIEW: DocumentStatus.AWAITING_REVIEW,
    JobState.CHUNKING: DocumentStatus.PROCESSING,
    JobState.EMBEDDING: DocumentStatus.PROCESSING,
    JobState.INDEXING: DocumentStatus.PROCESSING,
    JobState.COMPLETED: DocumentStatus.INDEXED,
    JobState.FAILED: DocumentStatus.FAILED,
    # Distinct de `pending` (2026-08-01) : un document dont le job vient d'être
    # annulé n'a plus rien « en attente », contrairement à ce qu'affichait la
    # confusion avec `pending` — il redevient ré-ingérable, mais l'interface
    # doit dire *annulé*, pas *en attente d'un traitement à venir*.
    JobState.CANCELLED: DocumentStatus.CANCELLED,
}
"""Projection `JobState` → `DocumentStatus`, écrite dans la même transaction que la transition."""


def is_terminal(state: JobState) -> bool:
    """Indique si `state` est un état terminal."""
    return state in TERMINAL_STATES


def can_transition(source: JobState, target: JobState) -> bool:
    """Indique si le saut `source` → `target` est légal."""
    return target in ALLOWED_TRANSITIONS[source]


def assert_transition(source: JobState, target: JobState) -> None:
    """Valide le saut `source` → `target`.

    Raises:
        ConflictError: si la transition est illégale (traduit en HTTP 409 par le
            contrat d'erreur RFC 9457).
    """
    if can_transition(source, target):
        return
    detail = (
        f"Transition d'état illégale : « {source.value} » ne peut pas devenir « {target.value} »."
    )
    # Les états acceptables voyagent dans le Problem Details : l'interface peut
    # afficher l'action réellement possible plutôt qu'un 409 opaque.
    extensions: dict[str, JsonValue] = {
        "current_state": source.value,
        "requested_state": target.value,
        "allowed_states": [state.value for state in sorted(ALLOWED_TRANSITIONS[source])],
    }
    raise ConflictError(detail, extensions=extensions)


def document_status_for(state: JobState) -> DocumentStatus:
    """Renvoie le `DocumentStatus` projeté par l'état de job `state`."""
    return DOCUMENT_STATUS_BY_JOB_STATE[state]
