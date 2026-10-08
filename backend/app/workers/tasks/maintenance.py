"""Balayage périodique de maintenance : jobs orphelins, décisions expirées, catégories de greffon.

Motif C de l'audit de dette 2026-09 (interne) : `beat_schedule`
(`app.workers.celery_app`) ne portait qu'une entrée avant ce module — la
répartition des évaluations programmées. Trois défauts distincts partageaient
la même cause (l'absence de tout balayage périodique) et sont donc réunis ici
plutôt que dispersés en trois tâches sans rapport apparent :

1. **Jobs orphelins** (`sweep_stalled_ingestion_job`). `embedding`/`indexing`
   ne sortent que via la veille anti-blocage évaluée **à l'intérieur** de
   `_poll` (`app.workers.tasks.ingestion`) : si la chaîne de sondage meurt
   (worker tué, message perdu), plus rien ne la réarme, et ni `cancel` ni
   `retry` (`app.services.jobs`) ne rattrapent un job dans l'un de ces deux
   états. Ce balayage détecte l'immobilité d'un job **depuis l'extérieur** de
   la chaîne de sondage, sans jamais recalculer la règle elle-même :
   `app.workers.tasks.ingestion.fail_stalled_job` (extraite de `_poll` pour
   cette raison précise) reste le seul point qui décide qu'un job est mort et
   l'écrit.

   Le seuil (`ragflow_stuck_job_timeout_seconds + ingestion_job_orphan_grace_
   seconds`) est choisi pour qu'un job encore activement sondé n'entre
   **jamais** dans le champ de ce balayage : une chaîne vivante réévalue la
   même immobilité à chaque sondage (`ragflow_poll_interval_seconds`, 5 s par
   défaut) et se serait donc déjà elle-même transitée vers `failed` bien avant
   que ce seuil élargi ne soit atteint. Un job encore visible ici ne peut donc
   **que** avoir perdu sa chaîne de sondage — `with_for_update()` sur la
   relecture couvre le résidu de fenêtre où les deux se chevauchent malgré
   tout (une chaîne vivante qui écrirait au même instant).

2. **`awaiting_review` sans décision** (`resume_expired_precheck_decision`).
   `domain/state_machine.py` et `app.workers.tasks.ingestion.decision_of`
   documentaient un timeout de décision qui n'existait nulle part (Motif B).
   Ce balayage republie `run_chunking` pour un job resté en attente au-delà de
   `awaiting_review_decision_timeout_hours` — **exactement** ce que fait
   `DocumentService.record_precheck_decision` pour une décision explicite
   (`app.services.documents`) : aucune transition n'est écrite ici, `_enter`
   (`app.workers.tasks.ingestion`) l'écrira lui-même à l'exécution de la tâche
   republiée, avec le même événement `ingestion_job_events` qu'une décision
   humaine. `document.precheck_decision` reste `NULL` : `decision_of` le
   traite déjà comme le profil neutre par défaut — documenté, jamais implémenté
   avant ce module.

3. **Catégories de greffon** (`MaintenanceStep`, ADR-069). Un greffon ajoute
   sa propre catégorie — sélection bornée, sous le même verrou, puis une tâche
   par item — sans second `beat` ni second verrou : `Plugin.maintenance_steps`.
   Sans greffon, le balayage s'arrête aux deux catégories ci-dessus.

**Une seule entrée `beat_schedule`, un seul verrou.** Toutes les sous-tâches
sont sélectionnées par une tâche unique (`run_maintenance_sweep`), protégée
par un verrou consultatif Postgres **non bloquant**
(`pg_try_advisory_xact_lock` — même famille que `pg_advisory_xact_lock`,
déjà utilisé par `app.workers.tasks.ingestion._chunk_document`, mais non
bloquant : un second `beat` qui publierait la même entrée en double renonce
proprement plutôt que d'attendre puis de dupliquer la sélection). Chaque
sous-tâche fan-out ensuite une tâche par item
(`sweep_stalled_ingestion_job`, `resume_expired_precheck_decision`, puis les
tâches des catégories de greffon) sur la file `default` — jamais `gpu` (CONTRIBUTING.md §2) —
avec la même philosophie « dispatch, jamais de boucle bloquante » que
`app.workers.tasks.evaluations.dispatch_scheduled_evaluations` : un item en
échec n'empêche jamais les suivants d'être traités, et chaque sélection est
bornée (`maintenance_sweep_batch_limit`, par catégorie) — un passage qui ne
suffit pas à tout traiter en laisse le reste au suivant, jamais un fan-out non
borné.

**Aucune tâche ne relève d'exception à Celery.** Même politique que
`app.workers.tasks.evaluations` pour sa routine programmée : un moteur à sec
le reste au prochain passage, le retenter immédiatement via l'autoretry
Celery n'aiderait pas et risquerait d'aggraver une panne déjà en cours. Chaque
tâche par item journalise puis avale, et la trace de ce qui reste à faire est
l'état de la base elle-même — jamais une table de suivi séparée.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.plugins import extra_maintenance_steps
from app.db.models import IngestionJob
from app.domain.enums import JobState
from app.workers.celery_app import (
    CHUNKING_TASK,
    CPU_TASK_OPTIONS,
    RESUME_EXPIRED_PRECHECK_DECISION_TASK,
    RUN_MAINTENANCE_SWEEP_TASK,
    SWEEP_STALLED_INGESTION_JOB_TASK,
    task,
    task_by_name,
)
from app.workers.session import run_in_session
from app.workers.tasks.ingestion import (
    EMBEDDING_STEP,
    INDEXING_STEP,
    Step,
    _engine_target,
    fail_stalled_job,
)

_logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY: Final = "ragengine.maintenance.sweep"
"""Clé du verrou consultatif : un nom stable, pas un identifiant de ligne — un
seul passage de sélection à la fois, tous items confondus (contrairement à
`pg_advisory_xact_lock(hashtext(knowledge_base_id))` dans `_chunk_document`,
qui sérialise par base)."""

_POLLING_STEP_BY_STATE: Final[dict[JobState, Step]] = {
    JobState.EMBEDDING: EMBEDDING_STEP,
    JobState.INDEXING: INDEXING_STEP,
}
"""Les deux seuls états sans sortie si la chaîne de sondage meurt (Motif C, item 1)."""

_STALLED_JOB_SWEEP_FAILURE_MARKER: Final = "maintenance.stalled_job_sweep_failed"
_PRECHECK_DECISION_TIMEOUT_FAILURE_MARKER: Final = "maintenance.precheck_decision_timeout_failed"


@dataclass(frozen=True, slots=True)
class MaintenanceStep:
    """Catégorie de balayage apportée par un greffon (`Plugin.maintenance_steps`).

    `select` choisit, sous le verrou du balayage, au plus `limit` identifiants
    à traiter ; `task_name` est publiée une fois par identifiant, **après** la
    fermeture de la boucle d'événements (même règle que les catégories du
    cœur). `name` est la clé de la catégorie dans le compte rendu du passage.
    """

    name: str
    select: Callable[[AsyncSession, Settings, int, datetime], Awaitable[Sequence[uuid.UUID]]]
    task_name: str


# ─── Verrou consultatif ───────────────────────────────────────────────────────


async def _try_lock(db: AsyncSession) -> bool:
    """Verrou non bloquant, borné à la transaction courante (`pg_try_advisory_xact_lock`).

    Renvoie `False` sans attendre si un autre passage le détient déjà — c'est
    ce qui distingue ce verrou de `pg_advisory_xact_lock`
    (`app.workers.tasks.ingestion._chunk_document`), qui bloque : deux workers
    `beat` publiant la même entrée en double ne doivent jamais faire attendre
    l'un des deux passages, seulement en annuler un proprement.
    """
    acquired = await db.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": _ADVISORY_LOCK_KEY}
    )
    return bool(acquired)


# ─── Item 1 : jobs d'ingestion orphelins ──────────────────────────────────────


async def _list_stalled_ingestion_job_ids(
    db: AsyncSession, *, threshold: datetime, limit: int
) -> Sequence[uuid.UUID]:
    """Jobs `embedding`/`indexing` immobiles depuis avant `threshold`, les plus anciens d'abord.

    `func.coalesce` retombe sur `updated_at` quand `engine_progress_observed_at`
    est encore `NULL` — ne devrait pas arriver depuis ces deux états (`_poll`
    l'écrit dès la première observation), mais reste le repère le moins faux en
    son absence plutôt qu'exclure le job de la sélection.
    """
    last_observed = func.coalesce(IngestionJob.engine_progress_observed_at, IngestionJob.updated_at)
    statement = (
        select(IngestionJob.id)
        .where(IngestionJob.state.in_(tuple(_POLLING_STEP_BY_STATE)), last_observed < threshold)
        .order_by(last_observed)
        .limit(limit)
    )
    return (await db.execute(statement)).scalars().all()


async def _sweep_stalled_ingestion_job(
    db: AsyncSession, job_id: uuid.UUID, settings: Settings
) -> str:
    """Relit un job orphelin candidat sous verrou de ligne, puis le fait échouer s'il l'est encore.

    `with_for_update()` sérialise cette relecture contre l'écriture qu'une
    chaîne de sondage encore vivante effectuerait au même instant (`_poll` →
    `IngestionJobService.transition`, qui verrouille implicitement la ligne le
    temps de son `UPDATE` même sans `with_for_update()` de son côté) : si cette
    chaîne a déjà fait progresser ou échouer le job, la relecture ci-dessous le
    voit et renonce (`"skipped"`) plutôt que de le re-décider sur une donnée
    périmée.
    """
    result = await db.execute(
        select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
    )
    job = result.scalar_one_or_none()
    if job is None or job.state not in _POLLING_STEP_BY_STATE:
        return "skipped"

    now = datetime.now(UTC)
    observed_at = job.engine_progress_observed_at or job.updated_at
    orphan_timeout = timedelta(
        seconds=settings.ragflow_stuck_job_timeout_seconds
        + settings.ingestion_job_orphan_grace_seconds
    )
    idle = now - observed_at
    if idle <= orphan_timeout:
        return "skipped"

    step = _POLLING_STEP_BY_STATE[job.state]
    target = await _engine_target(db, job)
    written = await fail_stalled_job(db, step, job_id, job, target, idle)
    return "failed" if written is not None else "skipped"


@task(name=SWEEP_STALLED_INGESTION_JOB_TASK, **CPU_TASK_OPTIONS)
def sweep_stalled_ingestion_job(job_id: str) -> str:
    """Fait échouer un job `embedding`/`indexing` orphelin (Motif C, item 1).

    Ne relève jamais d'exception à Celery : un échec moteur pendant
    `cancel_parsing` (`fail_stalled_job`) laisse le job intact, retenté au
    passage suivant, cf. la docstring du module.
    """
    settings = get_settings()
    identifier = uuid.UUID(job_id)
    try:
        return run_in_session(lambda db: _sweep_stalled_ingestion_job(db, identifier, settings))
    except Exception as error:  # avalé délibérément, cf. docstring du module.
        _logger.error(
            "Balayage d'un job orphelin en échec — nouvel essai au passage suivant",
            extra={
                "event": _STALLED_JOB_SWEEP_FAILURE_MARKER,
                "job_id": job_id,
                "error_type": type(error).__name__,
            },
        )
        return "failed"


# ─── Item 2 : décisions de precheck expirées ─────────────────────────────────


async def _list_expired_precheck_decision_ids(
    db: AsyncSession, *, threshold: datetime, limit: int
) -> Sequence[uuid.UUID]:
    """Jobs `awaiting_review` sans décision depuis avant `threshold`, les plus anciens d'abord."""
    statement = (
        select(IngestionJob.id)
        .where(IngestionJob.state == JobState.AWAITING_REVIEW, IngestionJob.updated_at < threshold)
        .order_by(IngestionJob.updated_at)
        .limit(limit)
    )
    return (await db.execute(statement)).scalars().all()


async def _resume_expired_precheck_decision(
    db: AsyncSession, job_id: uuid.UUID, settings: Settings
) -> datetime | None:
    """Décide si une reprise doit être publiée — ne publie jamais elle-même.

    Renvoie l'horodatage `awaiting_review_since` si la reprise est due, sinon
    `None`. **Ne fait aucun `.delay()`** : cette coroutine s'exécute dans la
    boucle d'événements ouverte par `run_in_session` (`asyncio.run`), et
    republier `run_chunking` depuis ici lui ferait tenter son propre
    `asyncio.run()` imbriqué — `RuntimeError` systématique sous Celery eager
    (donc à chaque test), silencieuse en production réelle uniquement parce
    qu'un déploiement n'y exécute jamais une tâche en ligne depuis `.delay()`.
    Même règle que `_dispatch_successor`
    (`app.workers.tasks.ingestion._run_step`) : la publication effective a lieu
    dans `resume_expired_precheck_decision`, la fonction synchrone qui
    l'appelle, **après** que `run_in_session` a refermé sa boucle.

    Aucune transition n'est écrite ici non plus : `_enter` (`app.workers.tasks.
    ingestion`) l'écrira à l'exécution de la tâche republiée, exactement comme
    pour une décision humaine explicite
    (`DocumentService.record_precheck_decision`). Rien n'est commité par cette
    fonction avant que l'appelant ne publie : elle ne lit que de l'état déjà
    durable (`awaiting_review` est écrit depuis longtemps), il n'y a donc pas
    de fenêtre où un worker republié lirait un état pas encore visible.
    """
    threshold = datetime.now(UTC) - timedelta(hours=settings.awaiting_review_decision_timeout_hours)
    result = await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None or job.state is not JobState.AWAITING_REVIEW or job.updated_at >= threshold:
        return None
    return job.updated_at


@task(name=RESUME_EXPIRED_PRECHECK_DECISION_TASK, **CPU_TASK_OPTIONS)
def resume_expired_precheck_decision(job_id: str) -> str:
    """Reprend un job `awaiting_review` resté sans décision (Motif B/C, item 2)."""
    settings = get_settings()
    identifier = uuid.UUID(job_id)
    try:
        awaiting_review_since = run_in_session(
            lambda db: _resume_expired_precheck_decision(db, identifier, settings)
        )
    except Exception as error:  # avalé délibérément, cf. docstring du module.
        _logger.error(
            "Reprise après timeout de décision en échec — nouvel essai au passage suivant",
            extra={
                "event": _PRECHECK_DECISION_TIMEOUT_FAILURE_MARKER,
                "job_id": job_id,
                "error_type": type(error).__name__,
            },
        )
        return "failed"

    if awaiting_review_since is None:
        return "skipped"

    # Après le commit de `run_in_session`, jamais avant (cf. docstring ci-dessus).
    task_by_name(CHUNKING_TASK).delay(job_id)
    _logger.info(
        "Décision de precheck expirée : reprise automatique avec le profil par défaut",
        extra={"job_id": job_id, "awaiting_review_since": awaiting_review_since.isoformat()},
    )
    return "resumed"


# ─── Dispatch ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _SweepSelection:
    """Ce qu'un passage doit publier — jamais publié depuis la coroutine qui la produit.

    `_dispatch` s'exécute dans la boucle d'événements ouverte par
    `run_in_session` : y appeler `.delay()` ferait tenter à la tâche
    republiée son propre `asyncio.run()` imbriqué (même piège que documenté
    sur `_resume_expired_precheck_decision`). Ces listes ne sont donc
    que des identifiants déjà sélectionnés ; `run_maintenance_sweep` les
    publie lui-même, une fois `run_in_session` revenu.
    """

    locked: bool
    stalled_ingestion_job_ids: Sequence[uuid.UUID] = ()
    expired_precheck_decision_ids: Sequence[uuid.UUID] = ()
    plugin_selections: Sequence[tuple[MaintenanceStep, Sequence[uuid.UUID]]] = field(
        default_factory=tuple
    )


async def _dispatch(db: AsyncSession, settings: Settings) -> _SweepSelection:
    """Sélectionne chaque catégorie de candidats, sous verrou — ne publie rien elle-même."""
    if not await _try_lock(db):
        _logger.info("Balayage de maintenance ignoré : un autre passage est déjà en cours")
        return _SweepSelection(locked=False)

    now = datetime.now(UTC)
    limit = settings.maintenance_sweep_batch_limit

    stalled_threshold = now - timedelta(
        seconds=settings.ragflow_stuck_job_timeout_seconds
        + settings.ingestion_job_orphan_grace_seconds
    )
    stalled_ids = await _list_stalled_ingestion_job_ids(
        db, threshold=stalled_threshold, limit=limit
    )

    decision_threshold = now - timedelta(hours=settings.awaiting_review_decision_timeout_hours)
    expired_decision_ids = await _list_expired_precheck_decision_ids(
        db, threshold=decision_threshold, limit=limit
    )

    plugin_selections = [
        (step, await step.select(db, settings, limit, now)) for step in extra_maintenance_steps()
    ]

    return _SweepSelection(
        locked=True,
        stalled_ingestion_job_ids=stalled_ids,
        expired_precheck_decision_ids=expired_decision_ids,
        plugin_selections=plugin_selections,
    )


@task(name=RUN_MAINTENANCE_SWEEP_TASK, **CPU_TASK_OPTIONS)
def run_maintenance_sweep() -> dict[str, int]:
    """Point d'entrée `beat_schedule` unique du balayage de maintenance (ADR-055).

    Ne fait elle-même aucune écriture métier : elle sélectionne des candidats
    bornés (`_dispatch`) puis publie une tâche par item
    (`sweep_stalled_ingestion_job`, `resume_expired_precheck_decision`, puis
    les tâches des catégories de greffon), cf. la docstring du module. La
    publication a lieu ici, dans cette fonction
    synchrone, **après** que `run_in_session` a refermé sa boucle d'événements
    — jamais dans `_dispatch` (même règle que `_dispatch_successor`,
    `app.workers.tasks.ingestion._run_step`) : republier une tâche depuis une
    coroutine qui tourne encore ferait tenter à celle-ci son propre
    `asyncio.run()` imbriqué.
    """
    selection = run_in_session(lambda db: _dispatch(db, get_settings()))
    if not selection.locked:
        return {"skipped": 1}

    for job_id in selection.stalled_ingestion_job_ids:
        sweep_stalled_ingestion_job.delay(str(job_id))
    for decision_job_id in selection.expired_precheck_decision_ids:
        resume_expired_precheck_decision.delay(str(decision_job_id))
    for step, identifiers in selection.plugin_selections:
        plugin_task = task_by_name(step.task_name)
        for identifier in identifiers:
            plugin_task.delay(str(identifier))

    counts = {
        "stalled_ingestion_jobs": len(selection.stalled_ingestion_job_ids),
        "expired_precheck_decisions": len(selection.expired_precheck_decision_ids),
    }
    counts.update(
        {step.name: len(identifiers) for step, identifiers in selection.plugin_selections}
    )
    _logger.info("Balayage de maintenance dispatché", extra=counts)
    return counts
