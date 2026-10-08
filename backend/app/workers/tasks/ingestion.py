"""Pipeline d'ingestion : une tâche Celery par étape de la machine à états.

Le job traverse `queued → prechecking → awaiting_review → chunking → embedding →
indexing → completed`. Deux familles d'étapes cohabitent, et elles n'ont pas la
même forme parce qu'elles ne pilotent pas la même chose.

**Étapes à travail borné** (`run_precheck`, `run_chunking`). Elles écrivent leur
transition d'entrée, font leur travail, puis concluent ou passent la main. Le
precheck analyse les octets relus du stockage ; le chunking est le **seul** point
d'engagement du moteur d'ingestion : dataset, téléversement, profil de découpage,
lancement du parsing.

**Étapes de sondage** (`run_embedding`, `run_indexing`). Le moteur n'expose qu'un
seul état discret et ne rappelle personne : c'est à RAGEngine d'aller voir. Ces
deux tâches **n'écrivent leur transition d'entrée que lorsque la condition est
remplie** ; tant qu'elle ne l'est pas, elles se replanifient elles-mêmes avec un
`countdown` et n'écrivent rien. Aucun `sleep` n'occupe un worker entre deux
sondages, et aucune ne fait de travail GPU : c'est le moteur qui appelle le
service d'inférence, de son côté (ADR-013).

Quatre règles gouvernent tout le module.

**1. Écrivain unique.** Aucune tâche n'écrit `IngestionJob.state` ni
`Document.status` : tout passe par `IngestionJobService.transition`, qui valide la
légalité du saut (`app.domain.state_machine`) et journalise l'événement dans la
même transaction. `progress_percent` reste à sa valeur : le `progress` du moteur
est un flottant bruité dans `[-1, 1]`, pas un pourcentage, et l'afficher comme
tel serait une donnée fausse (ADR-012).

**2. Garde d'idempotence.** `task_acks_late` fait redistribuer toute tâche non
confirmée : une tâche peut donc s'exécuter alors que le job a déjà avancé. Avant
toute transition, l'état courant est comparé à l'état source attendu :

- état == source ⇒ `EXECUTED` : cas nominal, la transition d'entrée est écrite ;
- état == target ⇒ `RESUMED` : l'entrée avait déjà été écrite, le worker est mort
  ensuite. On reprend la suite de l'étape **sans** ré-écrire la transition (donc
  sans doublon dans le journal) : c'est ce qui rend vrai « un kill du worker
  reprend proprement ». Pour une étape de sondage sans conclusion propre, cette
  situation signifie au contraire que la bascule a déjà eu lieu et que la chaîne
  de sondage continue ailleurs : la tâche s'arrête, sous peine de faire tourner
  deux chaînes en parallèle sur le même job ;
- tout autre état ⇒ `SKIPPED` : le job est ailleurs (annulé, échoué, ou déjà plus
  loin). La tâche se termine proprement, sans exception. Laisser
  `assert_transition` lever une `ConflictError` déclencherait un retry sur une
  erreur qui n'en est pas une.

**3. Échec = trois choses.** Une exception dans le corps d'une étape fait
d'abord passer le job à `failed` — avec un message exploitable dans
`IngestionJobEvent` *et* une cause structurée dans `IngestionJob.error`
(`JobError` : `code`, `retryable`, `retry_state`, cf. `_job_error_for`) —
journalise ensuite un repère de file morte distinct (`_dead_letter`, ADR-020),
**puis** est relancée pour que Celery applique sa politique de retry.
Conséquence assumée : `failed` étant terminal pour le pipeline automatique, un
retry Celery qui réussirait ne peut pas ressusciter le job ; il no-ope par la
garde ci-dessus — c'est `POST /api/v1/jobs/{id}/retry`
(`IngestionJobService.retry`), pas Celery, qui fait effectivement repartir un
job en échec, en le retransitant vers `JobError.retry_state` avant de
republier la tâche adéquate (`app.workers.celery_app.
RETRY_TASK_BY_SOURCE_STATE`).

**4. Chaînage explicite.** Chaque étape publie la suivante après avoir commité sa
propre transition (`.delay`), plutôt que d'assembler un `chain()` Celery à
l'ingestion. Trois raisons : le pipeline s'interrompt de toute façon sur
`awaiting_review`, qui attend une décision humaine ; un `chain` rejoué après échec
republierait toute la queue de la chaîne, là où un saut par saut laisse la garde
d'idempotence seul arbitre de la progression ; et l'ordre de publication — commit
d'abord, publication ensuite — garantit que le worker suivant lit un état déjà
écrit.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import Document, IngestionJob, KnowledgeBase
from app.domain.enums import JobState
from app.domain.schemas.document import PrecheckDecision
from app.domain.schemas.ingestion_job import JobError, JobWarning
from app.domain.state_machine import is_terminal
from app.ingestion.chunking_profile import (
    DOCLING_LAYOUT_RECOGNIZE,
    LAYOUT_RECOGNIZE_KEY,
    profile_for_decision,
)
from app.ingestion.precheck.analyzer import analyze_document_sync
from app.ingestion.precheck.schemas import PrecheckFormat
from app.ingestion.quality import (
    PDF_CONTENT_TYPE,
    evaluate_document_quality,
    extract_pdf_source_text,
)
from app.providers.engine import IngestionEnginePort, resolve_ingestion_engine
from app.providers.engine.base import (
    ChunkingProfile,
    EngineDatasetRef,
    EngineDocumentRef,
    EngineError,
    EngineParseStatus,
    EngineTransientError,
    EngineValidationError,
)
from app.providers.observability.base import ObservabilityPort
from app.providers.observability.factory import get_observability
from app.providers.storage import StoragePort, resolve_storage
from app.services.jobs import IngestionJobService
from app.workers.celery_app import (
    CHUNKING_TASK,
    CPU_TASK_OPTIONS,
    EMBEDDING_TASK,
    INDEXING_TASK,
    PRECHECK_TASK,
    task,
    task_by_name,
)
from app.workers.session import run_in_session

_logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_LENGTH: Final = 500
"""Le journal d'audit reçoit un message lisible, pas une trace complète."""

ENGINE_FAILURE_MESSAGE: Final = "Le moteur d'ingestion a signalé un échec de traitement."
ENGINE_CANCELLED_MESSAGE: Final = "Le traitement a été interrompu côté moteur d'ingestion."

DEAD_LETTER_MARKER: Final = "ingestion.dead_letter"
"""Valeur de `extra={"event": ...}` qui distingue un échec définitif de job de
tout autre log d'erreur — le point d'accroche d'une règle d'alerte côté
observabilité (agrégateur de logs, Langfuse…), sans mécanisme Celery
supplémentaire (ADR-020) : Redis, le broker de ce déploiement, n'a pas de
dead-lettering natif, et le signal `task_failure` de Celery ne se déclenche
presque jamais pour ce pipeline précis (vérifié, cf. `_mark_failed`) — le
journal structuré est donc la source de vérité opérationnelle, pas une
alternative dégradée à une file dédiée."""


class Outcome(StrEnum):
    """Issue d'une tâche, renvoyée telle quelle dans le backend de résultats."""

    EXECUTED = "executed"
    """Cas nominal : le job était dans l'état source attendu."""

    RESUMED = "resumed"
    """Reprise après crash : la transition d'entrée était déjà écrite."""

    WAITING = "waiting"
    """Sondage sans issue : le moteur n'a pas fini, la tâche s'est replanifiée."""

    SKIPPED = "skipped"
    """No-op d'idempotence : le job n'est plus concerné par cette étape."""


@dataclass(frozen=True, slots=True)
class Step:
    """Une étape du pipeline, décrite par ses seuls effets sur la machine à états."""

    name: str
    """Nom Celery de la tâche qui porte l'étape (clé de `TASK_ROUTES`)."""

    source: JobState
    """État attendu à l'entrée — c'est lui que compare la garde d'idempotence."""

    target: JobState
    """État écrit à l'entrée de l'étape."""

    entry_message: str
    """Message journalisé avec la transition d'entrée."""

    completion: JobState | None = None
    """État écrit en fin d'étape, quand l'étape se conclut elle-même."""

    completion_message: str | None = None

    successor: str | None = None
    """Nom de la tâche publiée en fin d'étape, quand le pipeline enchaîne."""

    engine_phases: frozenset[JobState] = frozenset()
    """Phases projetées par le moteur qui autorisent la transition d'entrée.

    Vide pour une étape à travail borné, qui n'interroge pas le moteur. Pour une
    étape de sondage, c'est la traduction de « le moteur est arrivé jusqu'ici » —
    volontairement **inclusive** (`embedding` accepte aussi `indexing`) : la
    projection est une heuristique et un sondage peut arriver après que le moteur
    a franchi deux marqueurs d'affilée.
    """


PRECHECK_STEP: Final = Step(
    name=PRECHECK_TASK,
    source=JobState.QUEUED,
    target=JobState.PRECHECKING,
    entry_message="Analyse préalable du document en cours.",
    completion=JobState.AWAITING_REVIEW,
    completion_message="Analyse préalable terminée : décision de l'utilisateur attendue.",
    # Aucun successeur : la reprise du pipeline est déclenchée par la décision
    # humaine (`PUT …/precheck/decision`), pas par cette tâche.
)

CHUNKING_STEP: Final = Step(
    name=CHUNKING_TASK,
    source=JobState.AWAITING_REVIEW,
    target=JobState.CHUNKING,
    entry_message="Découpage du document en cours.",
    successor=EMBEDDING_TASK,
)

EMBEDDING_STEP: Final = Step(
    name=EMBEDDING_TASK,
    source=JobState.CHUNKING,
    target=JobState.EMBEDDING,
    entry_message="Vectorisation des segments en cours.",
    successor=INDEXING_TASK,
    engine_phases=frozenset({JobState.EMBEDDING, JobState.INDEXING}),
)

INDEXING_STEP: Final = Step(
    name=INDEXING_TASK,
    source=JobState.EMBEDDING,
    target=JobState.INDEXING,
    entry_message="Indexation hybride en cours.",
    completion=JobState.COMPLETED,
    completion_message="Document indexé et interrogeable.",
    engine_phases=frozenset({JobState.INDEXING}),
)

PIPELINE_STEP_SOURCES: Final[frozenset[JobState]] = frozenset(
    {PRECHECK_STEP.source, CHUNKING_STEP.source, EMBEDDING_STEP.source, INDEXING_STEP.source}
)
"""Les quatre `Step.source`, exposés pour la reprise manuelle (ADR-020).

`app.domain.state_machine.RETRY_SOURCE_STATES` et
`app.workers.celery_app.RETRY_TASK_BY_SOURCE_STATE` doivent rester égaux à cet
ensemble — un test dédié (`tests/domain/test_state_machine.py`) le vérifie,
faute de pouvoir importer ce module depuis `app.domain` sans créer un cycle
(ce module importe déjà `app.services.jobs`)."""


# ─── Résolution des briques externes ─────────────────────────────────────────
# Trois fonctions plutôt que trois singletons de module : une tâche Celery ouvre
# sa propre boucle d'événements à chaque exécution, et un client HTTP rattaché à
# une boucle morte échouerait à l'exécution suivante.


def _settings() -> Settings:
    """Configuration du process worker."""
    return get_settings()


def _engine() -> IngestionEnginePort:
    """Moteur d'ingestion du déploiement — jamais RAGFlow nommément (CONTRIBUTING.md §3.3)."""
    return resolve_ingestion_engine(_settings())


def _storage() -> StoragePort:
    """Stockage des fichiers d'origine (ADR-014)."""
    return resolve_storage(_settings())


def _observability() -> ObservabilityPort:
    """Traçage applicatif du worker — no-op si Langfuse n'est pas configuré.

    `get_observability` mémoïse déjà l'instance Langfuse pour l'objet
    `Settings` du process (CONTRIBUTING.md §3.4, `providers/observability/factory.py`) :
    cette fonction reste un simple point de résolution, monkeypatchable en test
    exactement comme `_engine`/`_storage` ci-dessus.
    """
    return get_observability(_settings())


# ─── Chargement du contexte d'une étape ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _EngineTarget:
    """Le job, ses deux entités, et les références que le moteur leur connaît."""

    job: IngestionJob
    document: Document
    dataset: EngineDatasetRef
    document_ref: EngineDocumentRef


async def _document_of(db: AsyncSession, job: IngestionJob) -> Document:
    """Charge le document d'un job — la clé étrangère garantit son existence."""
    document = await db.get(Document, job.document_id)
    if document is None:  # pragma: no cover - la clé étrangère l'interdit
        raise ValueError(f"Le document du job {job.id} n'existe plus.")
    return document


async def _knowledge_base_of(db: AsyncSession, job: IngestionJob) -> KnowledgeBase:
    """Charge la base de connaissances d'un job, par sa clé dénormalisée."""
    knowledge_base = await db.get(KnowledgeBase, job.knowledge_base_id)
    if knowledge_base is None:  # pragma: no cover - la clé étrangère l'interdit
        raise ValueError(f"La base de connaissances du job {job.id} n'existe plus.")
    return knowledge_base


async def _engine_target(db: AsyncSession, job: IngestionJob) -> _EngineTarget:
    """Reconstitue les références du moteur, posées par `run_chunking`.

    Leur absence à ce stade n'est pas un aléa d'exécution mais un défaut de
    l'étape précédente : `ValueError` le dit, et cette classe d'erreur est
    exclue du retry Celery — la rejouer donnerait exactement le même manque.
    """
    document = await _document_of(db, job)
    knowledge_base = await _knowledge_base_of(db, job)
    if knowledge_base.ragflow_dataset_external_id is None:
        raise ValueError(f"Le job {job.id} n'a pas de dataset connu du moteur d'ingestion.")
    if document.ragflow_document_external_id is None:
        raise ValueError(f"Le job {job.id} n'a pas de document connu du moteur d'ingestion.")
    return _EngineTarget(
        job=job,
        document=document,
        dataset=EngineDatasetRef(external_id=knowledge_base.ragflow_dataset_external_id),
        document_ref=EngineDocumentRef(external_id=document.ragflow_document_external_id),
    )


# ─── Transitions ─────────────────────────────────────────────────────────────


async def _enter(db: AsyncSession, step: Step, job_id: uuid.UUID) -> Outcome:
    """Applique la garde d'idempotence, puis la transition d'entrée de l'étape."""
    service = IngestionJobService(db)
    job = await service.get(job_id)

    if job.state is step.target:
        _logger.info(
            "Étape reprise après interruption",
            extra={"job_id": str(job_id), "celery_task": step.name, "job_state": job.state.value},
        )
        return Outcome.RESUMED

    if job.state is not step.source:
        _logger.info(
            "Étape ignorée : le job n'est plus dans son état source",
            extra={
                "job_id": str(job_id),
                "celery_task": step.name,
                "job_state": job.state.value,
                "expected_state": step.source.value,
            },
        )
        return Outcome.SKIPPED

    await service.transition(job, step.target, message=step.entry_message)
    return Outcome.EXECUTED


@dataclass(frozen=True, slots=True)
class _QualityGuardResult:
    """Retour de `_quality_warning_for_step` : avertissement + compte de fragments.

    Les deux sont issus de la **même** lecture moteur (`list_document_chunks`)
    et suivent donc le même sort : soit les deux ont été calculés, soit
    aucun (`None`/`None`) — pas de cas intermédiaire où l'un serait connu et
    pas l'autre, ce qui garderait `chunk_count` cohérent avec l'échantillon
    ayant produit `warning` plutôt que de le faire dériver d'un appel séparé.
    """

    warning: JobWarning | None
    chunk_count: int | None


_NO_QUALITY_GUARD_RESULT: Final = _QualityGuardResult(warning=None, chunk_count=None)


async def _quality_warning_for_step(
    db: AsyncSession, step: Step, job: IngestionJob
) -> _QualityGuardResult:
    """Calcule le garde-fou qualité (ADR-024) — point de couture volontairement unique.

    Appelé par les **deux** endroits qui peuvent écrire `JobState.COMPLETED`
    pour l'étape d'indexation (`_poll`, cas rapide où l'entrée et la
    conclusion coïncident sur le même sondage, et `_complete`, cas normal) :
    factoriser ici est exactement ce qui évite la classe de bug « couture
    oubliée entre deux points d'appel qui semblaient indépendants » que l'ADR
    identifie. `step.completion is not JobState.COMPLETED` couvre le seul
    autre appelant de `_complete` (`PRECHECK_STEP`, conclusion vers
    `AWAITING_REVIEW`) : rien n'y est calculé.

    Porte aussi `Document.chunk_count` (Phase 13 bis) : `page.document.
    chunk_count` est le total réel côté moteur, non borné par l'échantillon
    `quality_guard_chunk_sample_limit` qui sert au calcul des métriques — la
    même lecture moteur renseigne gratuitement les deux.

    Ne lève **jamais** : une exception ici ne doit pas faire échouer un job
    qui a par ailleurs réussi techniquement (ADR-024, « ne bloque jamais ») —
    tout accroc (moteur injoignable, PDF illisible, stockage en panne) se
    traduit par « pas d'avertissement, pas de compte », journalisé, pas par un
    job en échec.
    """
    if step.completion is not JobState.COMPLETED:
        return _NO_QUALITY_GUARD_RESULT

    try:
        document = await _document_of(db, job)
        target = await _engine_target(db, job)
        settings = _settings()
        page = await _engine().list_document_chunks(
            target.dataset,
            target.document_ref,
            page_size=settings.quality_guard_chunk_sample_limit,
        )
        chunk_texts = [chunk.content for chunk in page.chunks]

        is_pdf = document.content_type == PDF_CONTENT_TYPE
        pdf_source_text: str | None = None
        if is_pdf:
            content = await _storage().read(document.storage_uri)
            pdf_source_text = extract_pdf_source_text(content)

        # Phase 13 : `use_parent_child` fait lire à `list_document_chunks` un
        # mélange de fragments enfants (courts par construction) et de
        # fragments « mère », sans marqueur exploitable pour les distinguer
        # dans cette réponse (cf. `quality.py::evaluate_quality`, docstring
        # `skip_degenerate_check`) — mesuré à 32 % de fragments « dégénérés »
        # sur un document par ailleurs sain (test de bout en bout, Phase 13).
        precheck_decision = document.precheck_decision or {}
        skip_degenerate_check = bool(precheck_decision.get("use_parent_child", False))

        warning = evaluate_document_quality(
            chunk_texts,
            is_pdf=is_pdf,
            pdf_source_text=pdf_source_text,
            settings=settings,
            skip_degenerate_check=skip_degenerate_check,
        )
        return _QualityGuardResult(warning=warning, chunk_count=page.document.chunk_count)
    except Exception:
        _logger.exception(
            "Calcul du garde-fou qualité en échec — job complété sans avertissement ni compte",
            extra={"job_id": str(job.id), "celery_task": step.name},
        )
        return _NO_QUALITY_GUARD_RESULT


async def _complete(db: AsyncSession, step: Step, job_id: uuid.UUID) -> None:
    """Écrit la transition de sortie, pour les étapes qui se concluent seules."""
    if step.completion is None:
        return

    service = IngestionJobService(db)
    job = await service.get(job_id)
    if job.state is not step.target:
        # Annulation ou échec concurrent pendant le corps de l'étape : le job a
        # changé de main, on ne lui impose pas notre conclusion.
        _logger.info(
            "Conclusion d'étape abandonnée : le job a changé d'état",
            extra={"job_id": str(job_id), "celery_task": step.name, "job_state": job.state.value},
        )
        return

    quality = await _quality_warning_for_step(db, step, job)
    await service.transition(
        job,
        step.completion,
        message=step.completion_message,
        warning=quality.warning,
        chunk_count=quality.chunk_count,
    )


async def _fail(
    db: AsyncSession, step: Step, job_id: uuid.UUID, error: JobError
) -> JobError | None:
    """Fait passer le job à `failed`, avec sa cause structurée. `None` s'il était déjà terminal.

    La valeur renvoyée est ce que l'appelant synchrone (`_run_step`,
    `_run_polling_step`) utilise pour déclencher la visibilité de file morte
    **après** le commit de `run_in_session` — jamais ici, dans la coroutine :
    même règle que `_dispatch_successor`/`_reschedule` (« Publication »,
    plus bas), pour ne jamais publier avant que l'état écrit ne soit visible.
    """
    service = IngestionJobService(db)
    job = await service.get(job_id)
    if is_terminal(job.state):
        return None
    await service.transition(job, JobState.FAILED, message=error.message, error=error)
    return error


def _failure_message(step: Step, error: BaseException) -> str:
    """Message d'échec destiné au journal d'audit — lisible, borné, sans trace."""
    detail = f"Échec de l'étape « {step.target.value} » : {type(error).__name__}: {error}"
    if len(detail) <= MAX_ERROR_MESSAGE_LENGTH:
        return detail
    return f"{detail[: MAX_ERROR_MESSAGE_LENGTH - 1]}…"


def _error_code_and_retryable(error: BaseException) -> tuple[str, bool]:
    """Classe une exception en `(code, retryable)` pour `JobError` (ADR-020).

    Seule `EngineTransientError` est retenue comme rejouable automatiquement :
    c'est la sous-classe que `providers/engine/ragflow/client.py` réserve aux
    pannes momentanées du moteur d'ingestion (réseau injoignable, ou codes
    RAGFlow 100/105/106 — « exception serveur générique », « erreur de
    connexion », « traitement déjà en cours », cf. sa docstring). Tout le
    reste — argument rejeté par le moteur (`EngineValidationError`), échec
    moteur ambigu (`EngineError` générique, ex. `code=102`), ou toute
    exception de programmation (`ValueError`, `NotFoundError`…) — n'a aucune
    raison d'aboutir si on la rejoue à l'identique. Un opérateur reste libre de
    forcer la reprise (`POST /jobs/{id}/retry?force=true`) s'il a corrigé la
    cause entre-temps.
    """
    if isinstance(error, EngineTransientError):
        return "engine_transient", True
    if isinstance(error, EngineValidationError):
        return "engine_validation", False
    if isinstance(error, EngineError):
        return "engine_error", False
    return "ingestion_step_failed", False


def _job_error_for(step: Step, error: BaseException) -> JobError:
    """Construit la cause structurée d'un échec survenu dans le corps d'une étape.

    `retry_state` vaut toujours `step.source` — jamais `step.target` : c'est
    l'état qu'il faut restaurer pour que l'étape en échec puisse être rejouée
    (`app.domain.state_machine.RETRY_SOURCE_STATES`), quel que soit l'endroit
    précis où l'exception est survenue dans le corps de l'étape.
    """
    code, retryable = _error_code_and_retryable(error)
    return JobError(
        code=code,
        message=_failure_message(step, error),
        retryable=retryable,
        retry_state=step.source,
    )


def _mark_failed(step: Step, job_id: uuid.UUID, error: BaseException) -> JobError | None:
    """Journalise l'échec côté job, sans jamais masquer l'exception d'origine.

    Le job doit refléter l'échec pour l'utilisateur même si l'écriture de cet
    échec échoue elle-même (base injoignable) : dans ce cas on trace et on
    laisse l'exception initiale remonter à Celery — c'est aussi, pour ce
    pipeline, le seul cas où le signal Celery natif (`task_failure`, une fois
    `max_retries` épuisé) porterait une information que ce journal ne porte
    pas déjà : tant que cette écriture réussit, toute tentative Celery
    postérieure à `failed` retombe sur la garde d'idempotence (`Outcome.
    SKIPPED`, tête de module) et se termine **sans exception** — un succès aux
    yeux de Celery, qui ne déclenche donc jamais `task_failure` pour le cas
    nominal (ADR-020).

    Renvoie la cause structurée si elle a bien été écrite (`None` si le job
    était déjà terminal, ou si l'écriture elle-même a échoué) : c'est ce que
    l'appelant utilise pour déclencher `_dead_letter` après le commit.
    """
    job_error = _job_error_for(step, error)
    try:
        written = run_in_session(lambda db: _fail(db, step, job_id, job_error))
    except Exception:
        _logger.exception(
            "Échec non journalisé en base : le job garde son état courant",
            extra={"event": DEAD_LETTER_MARKER, "job_id": str(job_id), "celery_task": step.name},
        )
        return None

    _logger.error(
        "Étape en échec" if written is not None else "Étape en échec sur un job déjà terminal",
        extra={"job_id": str(job_id), "celery_task": step.name, "reason": job_error.message},
    )
    return written


# ─── Publication ─────────────────────────────────────────────────────────────


def _dispatch_successor(step: Step, job_id: uuid.UUID) -> None:
    """Publie la tâche suivante du pipeline, une fois la transition commitée."""
    if step.successor is None:
        return

    # L'identifiant voyage en `str` : le sérialiseur est JSON (jamais pickle) et
    # c'est la forme que l'API passe elle aussi.
    task_by_name(step.successor).delay(str(job_id))
    _logger.info(
        "Étape suivante publiée",
        extra={"job_id": str(job_id), "celery_task": step.name, "successor": step.successor},
    )


def _dead_letter(*, job_id: uuid.UUID, celery_task: str, error: JobError) -> None:
    """Journalise la visibilité opérationnelle d'un échec définitif de job (DLQ, ADR-020).

    Complète `IngestionJob.state = failed` (déjà une trace applicative,
    lisible via `GET /api/v1/jobs/{id}`) par un signal distinct côté
    infra/observabilité — sans dupliquer la même information dans le modèle
    de données. Appelé une seule fois par échec réel : `_fail` n'écrit
    `job.error` qu'à la toute première transition vers `failed` (la garde
    d'idempotence renvoie `SKIPPED`/`Outcome.SKIPPED` sur toute tentative
    suivante, tête de module), donc ce log ne se répète jamais pour le même
    incident.
    """
    _logger.error(
        "Job en échec définitif",
        extra={
            "event": DEAD_LETTER_MARKER,
            "job_id": str(job_id),
            "celery_task": celery_task,
            "error_code": error.code,
            "retryable": error.retryable,
            "reason": error.message,
        },
    )


def _reschedule(step: Step, job_id: uuid.UUID) -> None:
    """Replanifie l'étape elle-même, après le délai de sondage configuré.

    C'est la seule forme d'attente autorisée : un `sleep` immobiliserait un
    worker pendant tout le parsing d'un document, là où une republication libère
    le créneau entre deux sondages (ADR-013).
    """
    countdown = _settings().ragflow_poll_interval_seconds
    task_by_name(step.name).apply_async(args=(str(job_id),), countdown=countdown)
    _logger.info(
        "Sondage du moteur replanifié",
        extra={"job_id": str(job_id), "celery_task": step.name, "countdown_seconds": countdown},
    )


# ─── Étapes à travail borné : precheck et chunking ───────────────────────────

type _StepBody = Callable[[AsyncSession, uuid.UUID], Awaitable[None]]
"""Corps d'une étape bornée : il travaille dans la session, sans rien transitionner."""


async def _precheck_document(db: AsyncSession, job_id: uuid.UUID) -> None:
    """Analyse le document et persiste son rapport de precheck.

    L'analyse ne lève jamais : un format inconnu ou un fichier illisible produit
    un rapport dégradé (`supported = False`), qui n'empêche pas l'utilisateur de
    décider — c'est le contrat de `app.ingestion.precheck`. Un échec de lecture
    du stockage, lui, est bien une erreur : sans octets, il n'y a rien à ingérer.
    """
    job = await IngestionJobService(db).get(job_id)
    document = await _document_of(db, job)

    content = await _storage().read(document.storage_uri)
    report = analyze_document_sync(content, document.filename, document.content_type)

    document.precheck_report = report.model_dump(mode="json")
    document.detected_languages = list(report.detected_languages)
    document.page_count = report.page_count
    await db.flush()

    _logger.info(
        "Precheck terminé",
        extra={
            "job_id": str(job_id),
            "document_id": str(document.id),
            "document_format": report.format.value,
            "detected_languages": list(report.detected_languages),
            "supported": report.supported,
        },
    )


async def _chunk_document(db: AsyncSession, job_id: uuid.UUID) -> None:
    """Engage le moteur d'ingestion : dataset, téléversement, profil, lancement.

    Unique point du pipeline qui écrit dans le moteur. Tout le reste — la
    vectorisation, l'indexation — s'y déroule ensuite sans que RAGEngine
    n'intervienne : les deux étapes suivantes ne font qu'observer.
    """
    job = await IngestionJobService(db).get(job_id)
    document = await _document_of(db, job)
    knowledge_base = await _knowledge_base_of(db, job)
    engine = _engine()

    known = knowledge_base.ragflow_dataset_external_id
    if known is None:
        # Coordination inter-tâches, payée une seule fois par base (première
        # ingestion) : sans elle, plusieurs chunkings concurrents sur une base
        # encore sans dataset lisent chacun `None`, et `ensure_dataset` en crée
        # un **par tâche** — RAGFlow ne déduplique jamais par nom, il renomme en
        # silence (ADR-012, décision 3). Un seul id gagne l'écriture sur
        # `KnowledgeBase.ragflow_dataset_external_id` ; les documents des
        # datasets perdants restent téléversés dans des datasets orphelins que
        # plus personne ne référence, et leur embedding échoue ensuite avec
        # `EngineError: You don't own the document` — trouvé en ingérant un lot
        # de documents en parallèle dans une base neuve (campagne E2E).
        #
        # Verrou transactionnel Postgres (`pg_advisory_xact_lock`, relâché au
        # commit de cette tâche — jamais explicitement, la portée `session_
        # scope`, `app/workers/session.py`, ne laisse qu'un commit par tâche) :
        # une tâche concurrente qui entre ici bloque jusqu'à ce que celle-ci
        # commite, puis revoit `known` déjà posé après le `refresh` ci-dessous.
        # Coût assumé : les chunkings concurrents d'une base neuve sont
        # sérialisés le temps de cette toute première résolution, jamais après
        # (`known` est déjà non `None` pour tout document suivant, le verrou
        # n'est même pas tenté).
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": str(knowledge_base.id)},
        )
        await db.refresh(knowledge_base)
        known = knowledge_base.ragflow_dataset_external_id

    dataset = await engine.ensure_dataset(
        ref=EngineDatasetRef(external_id=known) if known is not None else None,
        # L'UUID, jamais le nom lisible : un nom dupliqué n'est pas une erreur
        # côté moteur, il est renommé en silence (ADR-012, décision 3).
        name_seed=str(knowledge_base.id),
        embedding_provider_id=knowledge_base.embedding_model_id,
        reranker_provider_id=knowledge_base.reranker_model_id,
    )
    if known is None:
        knowledge_base.ragflow_dataset_external_id = dataset.external_id

    # Reprise après crash : le document avait déjà été téléversé, on le réutilise
    # au lieu d'en créer un doublon côté moteur.
    already_uploaded = document.ragflow_document_external_id
    if already_uploaded is not None:
        document_ref = EngineDocumentRef(external_id=already_uploaded)
    else:
        content = await _storage().read(document.storage_uri)
        document_ref = await engine.upload_document(
            dataset, filename=document.filename, content=content
        )
        document.ragflow_document_external_id = document_ref.external_id
    await db.flush()

    profile = profile_for_chunking(document)
    await engine.start_parsing(dataset, document_ref, profile)

    _logger.info(
        "Traitement lancé côté moteur d'ingestion",
        extra={
            "job_id": str(job_id),
            "document_id": str(document.id),
            "chunk_method": profile.chunk_method,
        },
    )


def decision_of(document: Document) -> PrecheckDecision:
    """Relit l'aiguillage persisté ; son absence vaut le profil par défaut.

    Un job repris après un timeout de décision
    (`app.workers.tasks.maintenance.resume_expired_precheck_decision`,
    `awaiting_review_decision_timeout_hours`, ADR-055), ou un document ingéré
    sans passer par l'écran d'aiguillage, arrive ici avec
    `precheck_decision = NULL` : le catalogue fermé n'ayant que des options
    optionnelles, cela donne exactement le profil neutre.
    """
    return PrecheckDecision.model_validate(document.precheck_decision or {})


def format_of(document: Document) -> PrecheckFormat:
    """Format détecté au precheck ; `UNSUPPORTED` par défaut si le rapport manque.

    `run_chunking` n'est atteignable qu'après un `run_precheck` conclu, qui
    écrit toujours `precheck_report` — l'absence ne devrait pas arriver, mais le
    choix le plus prudent en son absence est de ne **pas** supposer un tableur.
    """
    report = document.precheck_report or {}
    return PrecheckFormat(report.get("format", PrecheckFormat.UNSUPPORTED.value))


def is_likely_scanned_of(document: Document) -> bool:
    """Heuristique de scan détectée au precheck ; `True` par défaut si le rapport manque.

    Même prudence que `format_of` : l'absence ne devrait pas arriver, mais le
    choix le plus sûr en son absence est de supposer un scan — cela ne fait que
    reconduire l'ancien comportement (`DeepDOC` partout, `chunking_profile.py`).
    Le défaut inverse (supposer une couche texte propre) risquerait de faire
    sauter l'OCR sur un document qui en a réellement besoin, une perte de
    contenu bien plus coûteuse qu'un `DeepDOC` appliqué à tort.
    """
    report = document.precheck_report or {}
    return bool(report.get("is_likely_scanned", True))


def _layout_signal_of(document: Document, key: str) -> int:
    """Compteur d'aiguillage du rapport de precheck ; `0` s'il manque ou n'est pas un entier.

    Le défaut neutre n'est pas de la timidité : `0` ne route jamais vers
    `Docling` (`chunking_profile.py`), donc un rapport absent ou d'une version
    antérieure du schéma reconduit exactement le comportement d'avant ADR-043
    — jamais un profil dix fois plus lent choisi sur une donnée manquante.
    """
    value = (document.precheck_report or {}).get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def page_count_of(document: Document) -> int | None:
    """Nombre de pages relu du rapport de precheck ; `None` s'il est inconnu.

    `None` n'est pas une erreur : les formats non paginés n'en ont pas. Sur le
    chemin Docling, il dimensionne `task_page_size` et son absence retombe sur
    la borne haute du moteur (`DOCLING_SINGLE_TASK_PAGE_FALLBACK`).
    """
    value = (document.precheck_report or {}).get("page_count")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def profile_for_chunking(document: Document) -> ChunkingProfile:
    """Profil moteur d'un document — **point de lecture unique** du rapport de precheck.

    Appelée à deux moments distincts et pour deux besoins distincts : par
    `_chunk_document`, qui la **pose** sur le moteur, et par `_poll`, qui a
    besoin de savoir quel profil a été posé pour choisir son critère de fin
    (ADR-043, défaut n°2). Les deux doivent lire la même chose : un routage
    recalculé différemment de celui qui a été envoyé ferait sonder le mauvais
    critère, en silence. C'est une fonction pure d'un état persisté — le
    rapport de precheck ne bouge plus une fois écrit —, donc les deux appels
    rendent le même profil.
    """
    return profile_for_decision(
        decision_of(document),
        document_format=format_of(document),
        is_likely_scanned=is_likely_scanned_of(document),
        filename=document.filename,
        two_column_page_count=_layout_signal_of(document, "two_column_page_count"),
        table_count=_layout_signal_of(document, "table_count"),
        page_count=page_count_of(document),
    )


def _run_step(step: Step, job_id: str, body: _StepBody) -> str:
    """Exécute une étape bornée : garde, entrée, corps, conclusion, chaînage.

    `_enter` est délibérément **hors** du bloc protégé : tant que rien n'a été
    écrit, il n'y a pas d'échec à refléter sur le job, et un retry Celery
    repartira proprement de l'état source (c'est le cas d'une base
    momentanément injoignable).

    Encadrée par `ObservabilityPort.observe_ingestion_step` — point
    d'instrumentation unique pour toute la famille des étapes bornées
    (`run_precheck`, `run_chunking`) : aucun déclencheur existant
    (`_dispatch_successor`, transitions d'état) n'est déplacé ni modifié, ce
    bloc ne fait qu'observer ce qui s'exécutait déjà.
    """
    identifier = uuid.UUID(job_id)
    observability = _observability()
    with observability.observe_ingestion_step(
        job_id=identifier, state=step.target, celery_task=step.name
    ):
        outcome = run_in_session(lambda db: _enter(db, step, identifier))
        if outcome is Outcome.SKIPPED:
            observability.record_event("ingestion.step.outcome", outcome=outcome.value)
            return outcome.value

        try:
            run_in_session(lambda db: body(db, identifier))
            run_in_session(lambda db: _complete(db, step, identifier))
            _dispatch_successor(step, identifier)
        except Exception as error:
            written = _mark_failed(step, identifier, error)
            if written is not None:
                _dead_letter(job_id=identifier, celery_task=step.name, error=written)
            raise

        observability.record_event("ingestion.step.outcome", outcome=outcome.value)
        _logger.info(
            "Étape terminée",
            extra={"job_id": job_id, "celery_task": step.name, "outcome": outcome.value},
        )
        return outcome.value


# ─── Étapes de sondage : embedding et indexing ───────────────────────────────


class _Next(StrEnum):
    """Ce que la tâche doit publier après le commit de son sondage."""

    NOTHING = "nothing"
    """Rien à publier : l'étape est close (conclue, échouée, ou hors de propos)."""

    RETRY = "retry"
    """Le moteur n'a pas fini : la tâche se replanifie elle-même."""

    SUCCESSOR = "successor"
    """La condition est remplie : l'étape suivante prend le relais."""


@dataclass(frozen=True, slots=True)
class _Polled:
    """Issue d'un sondage, et la publication qu'elle appelle une fois commitée."""

    outcome: Outcome
    action: _Next
    failure: JobError | None = None
    """Cause structurée si ce sondage a fait échouer le job, sinon `None`.

    Distinct d'`action` : un échec produit toujours `_Next.NOTHING` (rien à
    publier), mais `_Next.NOTHING` recouvre aussi les cas neutres (conclusion
    normale, `SKIPPED`) qui n'ont pas de visibilité de file morte à déclencher.
    `_run_polling_step` s'en sert, une fois le commit passé, exactement comme
    `_run_step` se sert du retour de `_mark_failed`."""


def _observe(job: IngestionJob, status: EngineParseStatus, timeout: timedelta) -> timedelta | None:
    """Met à jour les témoins de vivacité ; renvoie l'immobilité si elle excède `timeout`.

    Le délai de garde se mesure depuis la **dernière évolution observée**, jamais
    depuis le début du job : un document volumineux a le droit d'être lent, et le
    faire échouer pour cette raison serait un faux positif garanti (ADR-012).

    L'écriture des trois colonnes a lieu même quand la tâche se contentera de se
    replanifier — sans elle, la veille n'aurait aucune origine à comparer et ne
    détecterait jamais rien.
    """
    now = datetime.now(UTC)
    observed = (status.progress, status.last_message)
    if job.engine_progress_observed_at is None or observed != (
        job.engine_progress,
        job.engine_last_message,
    ):
        job.engine_progress = status.progress
        job.engine_last_message = status.last_message
        job.engine_progress_observed_at = now
        return None

    idle = now - job.engine_progress_observed_at
    return idle if idle > timeout else None


def _stalled_message(idle: timedelta) -> str:
    """Message d'échec d'un blocage, en secondes entières."""
    return f"Le moteur d'ingestion n'a plus progressé depuis {int(idle.total_seconds())}s."


async def fail_stalled_job(
    db: AsyncSession,
    step: Step,
    job_id: uuid.UUID,
    job: IngestionJob,
    target: _EngineTarget,
    idle: timedelta,
) -> JobError | None:
    """Interrompt le moteur puis fait échouer un job resté immobile depuis `idle`.

    Point de règle **unique** de la veille anti-blocage (ADR-012) : `_poll`
    l'appelle dès que `_observe` détecte l'immobilité en cours de sondage, à
    partir d'une observation fraîche du moteur. `app.workers.tasks.maintenance.
    sweep_stalled_ingestion_job` l'appelle pour un job dont la chaîne de
    sondage a **disparu** (worker tué, message perdu — Motif C, `docs/
    l'audit de dette 2026-09 (interne)) et que `_poll` ne reverra donc jamais :
    il calcule `idle` depuis l'ancienneté de la dernière observation connue en
    base, sans appel moteur. Les deux appelants calculent `idle` différemment
    mais partagent la même décision et la même écriture — extrait de `_poll`
    pour cette raison précise, plutôt que reformulé une seconde fois côté
    balayage.

    Un blocage n'est pas imputé au moteur lui-même (`EngineTransientError` ne
    couvre que ses pannes déclarées) : reprendre à l'identique rejouerait le
    même parsing sur le même document, avec le même risque de se figer à
    nouveau. Non retryable par défaut ; `force=true` reste possible si la
    cause (document, charge du moteur) a été traitée.

    Ne rattrape aucune exception : un échec de `cancel_parsing` doit remonter
    tel quel à l'appelant — exactement le comportement d'avant cette
    extraction pour `_poll` (`_run_polling_step` le convertit alors en échec
    générique du job), et un choix tout aussi délibéré pour le balayage de
    maintenance, qui préfère reporter l'item au passage suivant plutôt que
    d'écrire un échec sur la foi d'une panne moteur transitoire.
    """
    await _engine().cancel_parsing(target.dataset, target.document_ref)
    stalled = JobError(
        code="engine_stalled",
        message=_stalled_message(idle),
        retryable=False,
        retry_state=step.source,
    )
    written = await _fail(db, step, job_id, stalled)
    _logger.error(
        "Traitement interrompu par la veille anti-blocage",
        extra={
            "job_id": str(job_id),
            "celery_task": step.name,
            "idle_seconds": int(idle.total_seconds()),
            "engine_last_message": job.engine_last_message,
        },
    )
    return written


def _engine_finish_is_trustworthy(status: EngineParseStatus, document: Document) -> bool:
    """`run == "done"` veut-il dire « l'index est écrit » pour **ce** document ?

    Il le veut dire pour `DeepDOC` et `Plain Text`, et c'est vérifiable dans le
    moteur : leur parseur ne rend la main qu'à la fin du parsing, la boucle de
    `rag/svr/task_executor.py` enchaîne découpage, embedding, écriture ES puis
    `increment_chunk_num`, et **c'est seulement ensuite** que le dernier
    `progress_callback(prog=1.0)` porte la tâche à 1,0. Le rapprochement
    périodique (`DocumentService._sync_progress`) ne pose `run = DONE` que
    lorsque *toutes* les tâches sont à 1,0 : quand RAGEngine le lit, tout est
    écrit.

    Il ne le veut **pas** dire pour `Docling` : `DoclingParser.parse_pdf()`
    émet `callback(1.0, "[Docling] Done.")` à la fin du *parsing*, donc avant
    le découpage, l'embedding et l'écriture ES (ADR-043, défaut n°2). La tâche
    est alors à 1,0 alors que le travail continue, le document passe `DONE`, et
    un job qui ne sonderait que cet état serait marqué `completed` sur un index
    vide — mesuré : 7 s d'écart sur un document de deux pages, et une passe
    rendue `done` avec **0 fragment**. Sur un document réel, la fenêtre se
    compte en minutes.

    Le signal de remplacement est `chunk_count`, et il est choisi parce qu'il
    est écrit **après** l'insertion, jamais avant : `increment_chunk_num` suit
    `insert_chunks` dans la même fonction du moteur (vérifié dans la source
    déployée), et il ne transite pas par le rapprochement de progression — il
    continue donc d'être mis à jour même après que le document est passé
    `DONE`. Un fragment présent prouve que l'index a reçu quelque chose ; c'est
    exactement la promesse que `completed` fait à l'utilisateur.

    Strictement additif : hors Docling, la fonction rend `True` sans rien
    regarder, et les deux autres profils gardent le comportement d'avant.

    Cas limite assumé — un document Docling qui terminerait réellement sans
    aucun fragment n'est jamais conclu ici. Il n'est pas perdu pour autant : la
    veille anti-blocage (`_observe`, `ragflow_stuck_job_timeout_seconds`, 900 s
    par défaut) le fait échouer plutôt que de le laisser tourner sans fin. Un
    job en échec est le bon état pour ce document : `completed` avec zéro
    fragment interrogeable serait un mensonge, et c'est ce mensonge que cette
    fonction existe pour empêcher.
    """
    profile = profile_for_chunking(document)
    if profile.parser_config.get(LAYOUT_RECOGNIZE_KEY) != DOCLING_LAYOUT_RECOGNIZE:
        return True
    return status.chunk_count > 0


async def _poll(db: AsyncSession, step: Step, job_id: uuid.UUID) -> _Polled:
    """Sonde le moteur une fois et en tire au plus une transition.

    Le sondage est le seul endroit du pipeline où une exécution peut n'écrire
    **rien du tout** : c'est ce qui autorise à replanifier la même tâche
    indéfiniment sans polluer le journal du job.
    """
    service = IngestionJobService(db)
    job = await service.get(job_id)

    if job.state is step.completion:
        # L'étape a déjà conclu (redélivrance d'un message tardif) : terminal.
        return _skipped(step, job)
    if job.state is step.target:
        if step.completion is None:
            # Étape sans conclusion propre : sa bascule est déjà écrite, donc une
            # autre chaîne de sondage court déjà. En relancer une seconde ferait
            # sonder le même document à deux endroits.
            return _skipped(step, job)
        entering = False
    elif job.state is step.source:
        entering = True
    else:
        return _skipped(step, job)

    target = await _engine_target(db, job)
    engine = _engine()
    status = await engine.get_parse_status(target.dataset, target.document_ref)

    settings = _settings()
    idle = _observe(job, status, timedelta(seconds=settings.ragflow_stuck_job_timeout_seconds))
    if idle is not None:
        # Le moteur ne s'en sortira pas seul (ADR-012, fait 4) : on l'interrompt
        # nous-mêmes, puis on fait échouer le job — un blocage n'est pas une
        # annulation demandée par l'utilisateur, donc `failed` et non `cancelled`.
        written = await fail_stalled_job(db, step, job_id, job, target, idle)
        return _Polled(Outcome.EXECUTED, _Next.NOTHING, failure=written)

    if status.run == "fail":
        # `retryable=False` reste juste malgré ADR-044 §5.1 (correction du
        # 2026-09-13) : le seul défaut moteur connu qui corrompt silencieusement
        # un envoi (RAGFlow servi par Werkzeug sous requêtes concurrentes) est
        # désormais rejoué **avant** ce point, dans l'adaptateur
        # (`RagflowEngineAdapter.upload_document`, vérification d'intégrité +
        # rejeu borné) — un document dont l'upload était corrompu n'atteint
        # `start_parsing` qu'une fois son contenu confirmé intact, donc ne
        # produit plus ce `run == "fail"` pour cette cause précise. Un
        # `engine_processing_failed` qui atteint quand même ce point est soit un
        # fichier réellement invalide pour le moteur (échec permanent, rejouer
        # à l'identique échouerait pareil), soit une panne échappant à cette
        # vérification (upload dont le rejeu s'est épuisé, ou corruption d'un
        # appel que l'adaptateur ne vérifie pas — `start_parsing` n'envoie que
        # du JSON, jamais observé corrompu). Dans les deux cas, rejouer
        # automatiquement en boucle ici serait le défaut que le rejeu de
        # l'adaptateur existe justement pour éviter ; `POST /jobs/{id}/retry`
        # reste le geste explicite qui reprend un job dans cet état.
        engine_failure = JobError(
            code="engine_processing_failed",
            message=status.error or ENGINE_FAILURE_MESSAGE,
            retryable=False,
            retry_state=step.source,
        )
        written = await _fail(db, step, job_id, engine_failure)
        return _Polled(Outcome.EXECUTED, _Next.NOTHING, failure=written)
    if status.run == "cancel":
        # Interruption décidée hors de RAGEngine : sans cette branche, la tâche
        # se replanifierait sur un document que plus rien ne fera avancer.
        engine_cancellation = JobError(
            code="engine_processing_cancelled",
            message=ENGINE_CANCELLED_MESSAGE,
            retryable=False,
            retry_state=step.source,
        )
        written = await _fail(db, step, job_id, engine_cancellation)
        return _Polled(Outcome.EXECUTED, _Next.NOTHING, failure=written)

    # `run == "done"` ne suffit pas sur le chemin Docling : le moteur y annonce
    # la fin avant d'avoir écrit l'index (ADR-043, défaut n°2). Le sondage
    # continue tant que rien n'est interrogeable, plutôt que de conclure sur du
    # vide. Aucune des deux autres valeurs de `layout_recognize` n'est touchée.
    finished = status.run == "done" and _engine_finish_is_trustworthy(status, target.document)
    if entering:
        if not (finished or status.inferred_phase in step.engine_phases):
            return _Polled(Outcome.WAITING, _Next.RETRY)
        message = status.last_message or step.entry_message
        await service.transition(job, step.target, message=message)
        if step.completion is None:
            return _Polled(Outcome.EXECUTED, _Next.SUCCESSOR)
        if finished:
            # Les deux conditions ont coïncidé sur ce même sondage — cas courant
            # d'un document qui termine vite. Écrire les deux transitions à la
            # suite ne saute aucun état : ce sont les deux transitions propres à
            # cette étape. `_quality_warning_for_step` no-ope pour toute étape
            # dont `completion` n'est pas `JobState.COMPLETED` (ADR-024).
            quality = await _quality_warning_for_step(db, step, job)
            await service.transition(
                job,
                step.completion,
                message=step.completion_message,
                warning=quality.warning,
                chunk_count=quality.chunk_count,
            )
            return _Polled(Outcome.EXECUTED, _Next.NOTHING)
        return _Polled(Outcome.EXECUTED, _Next.RETRY)

    # Transition d'entrée déjà écrite : on ne sonde plus que la conclusion.
    if not finished:
        return _Polled(Outcome.WAITING, _Next.RETRY)
    await _complete(db, step, job_id)
    return _Polled(Outcome.RESUMED, _Next.NOTHING)


def _skipped(step: Step, job: IngestionJob) -> _Polled:
    """No-op d'idempotence, journalisé pour rester diagnosticable."""
    _logger.info(
        "Sondage ignoré : le job n'est plus concerné par cette étape",
        extra={
            "job_id": str(job.id),
            "celery_task": step.name,
            "job_state": job.state.value,
            "expected_state": step.source.value,
        },
    )
    return _Polled(Outcome.SKIPPED, _Next.NOTHING)


def _run_polling_step(step: Step, job_id: str) -> str:
    """Exécute un sondage complet : lecture du moteur, au plus une transition, publication.

    La publication a lieu **après** le commit, comme pour les étapes bornées :
    le worker qui reprendra la main lit alors un état déjà écrit.

    Encadrée par `ObservabilityPort.observe_ingestion_step` — point
    d'instrumentation unique pour toute la famille des étapes de sondage
    (`run_embedding`, `run_indexing`) : aucun déclencheur existant
    (`_dispatch_successor`, `_reschedule`, transitions d'état) n'est déplacé ni
    modifié, ce bloc ne fait qu'observer ce qui s'exécutait déjà. Une exécution
    `WAITING` (le cas le plus fréquent, un sondage sans issue toutes les
    `ragflow_poll_interval_seconds`) produit donc un span court par tentative —
    c'est la conséquence attendue d'une tâche Celery par sondage, pas un défaut
    de cette instrumentation.
    """
    identifier = uuid.UUID(job_id)
    observability = _observability()
    with observability.observe_ingestion_step(
        job_id=identifier, state=step.target, celery_task=step.name
    ):
        try:
            polled = run_in_session(lambda db: _poll(db, step, identifier))
        except Exception as error:
            written = _mark_failed(step, identifier, error)
            if written is not None:
                _dead_letter(job_id=identifier, celery_task=step.name, error=written)
            raise

        if polled.action is _Next.SUCCESSOR:
            _dispatch_successor(step, identifier)
        elif polled.action is _Next.RETRY:
            _reschedule(step, identifier)
        elif polled.failure is not None:
            _dead_letter(job_id=identifier, celery_task=step.name, error=polled.failure)

        observability.record_event(
            "ingestion.step.outcome", outcome=polled.outcome.value, action=polled.action.value
        )
        _logger.info(
            "Sondage terminé",
            extra={"job_id": job_id, "celery_task": step.name, "outcome": polled.outcome.value},
        )
        return polled.outcome.value


# ─── Tâches Celery ───────────────────────────────────────────────────────────


@task(name=PRECHECK_TASK, **CPU_TASK_OPTIONS)
def run_precheck(job_id: str) -> str:
    """Analyse préalable — file `default` : `queued → prechecking → awaiting_review`.

    Aucun modèle, aucun GPU : l'analyse est locale, bornée et purement CPU
    (ADR-013). Se conclut sur `awaiting_review` sans publier de successeur —
    c'est la décision humaine qui relancera le pipeline en appelant
    `run_chunking`.
    """
    return _run_step(PRECHECK_STEP, job_id, _precheck_document)


@task(name=CHUNKING_TASK, **CPU_TASK_OPTIONS)
def run_chunking(job_id: str) -> str:
    """Découpage — file `default` : `awaiting_review → chunking`, puis `run_embedding`.

    Seule étape qui écrit dans le moteur d'ingestion. Le découpage réel s'y
    déroule ensuite ; côté RAGEngine, il ne reste que des appels HTTP.
    """
    return _run_step(CHUNKING_STEP, job_id, _chunk_document)


@task(name=EMBEDDING_TASK, **CPU_TASK_OPTIONS)
def run_embedding(job_id: str) -> str:
    """Vectorisation — file `default` : sonde jusqu'à `chunking → embedding`.

    La vectorisation elle-même est faite par le moteur, qui appelle le service
    d'inférence de son côté : cette tâche n'occupe aucune VRAM et ne fait
    qu'observer (ADR-013). Tant que le moteur n'a pas franchi la phase, elle se
    replanifie sans rien écrire ; une fois la bascule faite, elle passe la main à
    `run_indexing`.
    """
    return _run_polling_step(EMBEDDING_STEP, job_id)


@task(name=INDEXING_TASK, **CPU_TASK_OPTIONS)
def run_indexing(job_id: str) -> str:
    """Indexation — file `default` : sonde jusqu'à `embedding → indexing → completed`.

    Dernière étape du chemin nominal : elle écrit l'état terminal `completed`,
    qui projette `Document.status = indexed`. Les deux transitions peuvent tomber
    sur le même sondage quand le moteur termine vite.
    """
    return _run_polling_step(INDEXING_STEP, job_id)
