"""Application Celery de RAGEngine : broker, routage des files, politiques d'exécution.

**Le nom de l'instance est imposé.** `docker/docker-compose.yml` lance ses
workers avec `celery -A app.workers.celery_app` : sans attribut précisé, Celery
cherche par convention un objet nommé `app` dans ce module. Renommer
l'instance casserait le déploiement déjà écrit.

Routage — **une seule table déclarative** (`task_routes`), aucun `queue=` sur
les décorateurs. La question « quel matériel exécute quelle étape ? » se lit
alors en un seul endroit, en regard des `--queues=` des fichiers Compose.
`task_default_queue = "default"` complète la garantie : une tâche absente de la
table tombe sur le CPU, jamais sur le GPU. L'anti-OOM de CONTRIBUTING.md §2 reste
ainsi une propriété **de construction** — il faut une ligne explicite dans
`TASK_ROUTES` pour consommer de la VRAM.

Affectation — **les quatre étapes du pipeline sont CPU** (ADR-013) :

| Tâche           | File      | Motif                                                |
|-----------------|-----------|------------------------------------------------------|
| `run_precheck`  | `default` | analyse légère locale, sans modèle ni GPU             |
| `run_chunking`  | `default` | appels HTTP au moteur d'ingestion (upload, lancement) |
| `run_embedding` | `default` | sondage HTTP de l'état du moteur                      |
| `run_indexing`  | `default` | sondage HTTP de l'état du moteur                      |

Le precheck de RAGEngine est délibérément sans dépendance GPU, et c'est **le
moteur d'ingestion qui appelle lui-même le service d'inférence**, de son côté :
aucune de ces quatre tâches ne consomme de VRAM dans le process worker. Les
router sur `gpu` gaspillerait l'unique créneau de concurrence 1 sur du travail
réseau (ADR-013).

`GPU_QUEUE` et `GPU_TASK_OPTIONS` restent définis mais **inutilisés ici** : ils
serviront aux appels *directs* de RAGEngine à `EmbeddingPort`/`RerankerPort`
(reranking local optionnel du retrieval, Phase 5), seul chemin qui consommera
réellement de la VRAM depuis un worker.

Célery Beat — **premier usage dans ce dépôt** (Phase 12, Étape 4). `beat_schedule`
publie périodiquement `app.workers.tasks.evaluations.dispatch_scheduled_evaluations`,
qui fait elle-même le fan-out vers une tâche par base éligible
(`run_scheduled_evaluation`, elle aussi sur `default` : aucun appel n'y est GPU,
seulement du réseau vers RAGFlow et le fournisseur LLM). Le déploiement du
processus `celery beat` lui-même (service `worker-beat`,
`docker/docker-compose.yml`) est distinct des workers qui consomment les files :
un seul `beat` doit tourner par déploiement, sous peine de publier chaque run en
double.

**Deuxième entrée : le balayage de maintenance** (ADR-055,
l'audit de dette 2026-09 (interne), Motif C). `run_maintenance_sweep`
(`app.workers.tasks.maintenance`) couvre deux défauts qui partageaient la même
cause — l'absence de tout balayage périodique avant cette entrée : des jobs
`embedding`/`indexing` orphelins (chaîne de sondage disparue, plus rattrapables
ni par `cancel` ni par `retry`) et des jobs `awaiting_review` sans décision
humaine qui s'accumulaient sans jamais expirer ; un greffon y ajoute ses propres
catégories (`Plugin.maintenance_steps`, ADR-069). Même patron « dispatch +
fan-out » que la routine d'évaluation : la tâche publiée par `beat_schedule` ne
fait que sélectionner des candidats bornés (`maintenance_sweep_batch_limit`
par catégorie) et publier une tâche par item sur `default` — un item en échec
n'empêche jamais les suivants. Protégée par un verrou consultatif Postgres non
bloquant (`pg_try_advisory_xact_lock`) : un second `beat` qui publierait la
même entrée en double renonce proprement plutôt que de dupliquer la sélection.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import timedelta
from importlib import import_module
from typing import Final, Protocol, cast

from celery import Celery  # type: ignore[import-untyped]
from celery.signals import setup_logging, worker_shutdown  # type: ignore[import-untyped]

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import configure_logging
from app.core.plugins import extra_task_modules, extra_task_routes
from app.domain.enums import JobState
from app.workers.session import dispose_engine

_logger = logging.getLogger(__name__)

DEFAULT_QUEUE: Final = "default"
"""File CPU : orchestration, I/O, parsing léger, polling RAGFlow."""

GPU_QUEUE: Final = "gpu"
"""File GPU : `concurrency=1`, `prefetch=1` — sémaphore VRAM (CONTRIBUTING.md §2).

Aucune tâche du pipeline d'ingestion n'y est routée (ADR-013) : elle est
réservée aux appels **directs** de RAGEngine au service d'inférence, à venir en
Phase 5. Elle reste déclarée ici pour que ce jour-là le routage n'ait qu'une
ligne à gagner, jamais une convention à réinventer.
"""

INGESTION_NAMESPACE: Final = "ragengine.ingestion"
"""Préfixe des noms de tâches : lisible dans Flower, stable en cas de refactor."""

PRECHECK_TASK: Final = f"{INGESTION_NAMESPACE}.run_precheck"
CHUNKING_TASK: Final = f"{INGESTION_NAMESPACE}.run_chunking"
EMBEDDING_TASK: Final = f"{INGESTION_NAMESPACE}.run_embedding"
INDEXING_TASK: Final = f"{INGESTION_NAMESPACE}.run_indexing"

EVALUATION_NAMESPACE: Final = "ragengine.evaluations"
"""Préfixe des noms de tâches de la routine d'évaluation programmée (Phase 12 Étape 4)."""

DISPATCH_SCHEDULED_EVALUATIONS_TASK: Final = (
    f"{EVALUATION_NAMESPACE}.dispatch_scheduled_evaluations"
)
RUN_SCHEDULED_EVALUATION_TASK: Final = f"{EVALUATION_NAMESPACE}.run_scheduled_evaluation"
RUN_ASYNC_EVALUATION_TASK: Final = f"{EVALUATION_NAMESPACE}.run_async_evaluation"
"""Run déclenché à la demande (`POST .../evaluations`, `sample_size` au-delà du
plafond synchrone) — Phase 12 Étape 4 (item restant), ADR-028. Sœur de
`RUN_SCHEDULED_EVALUATION_TASK`, pas une généralisation de celle-ci : elle
reçoit un `EvaluationRun` déjà persisté en `pending` (`knowledge_base_id`,
`evaluation_run_id`) plutôt que de résoudre elle-même `sample_size`/
`llm_provider_id`/le principal depuis `evaluation_enabled` — ce sont
`EvaluationService.create_pending_run` et la ligne déjà écrite qui portent
ces valeurs, explicites, à la création du run."""

MAINTENANCE_NAMESPACE: Final = "ragengine.maintenance"
"""Préfixe des noms de tâches du balayage de maintenance (ADR-055, Motif C)."""

RUN_MAINTENANCE_SWEEP_TASK: Final = f"{MAINTENANCE_NAMESPACE}.run_maintenance_sweep"
"""Point d'entrée `beat_schedule` unique : sélectionne, fan-out, ne traite rien elle-même."""

SWEEP_STALLED_INGESTION_JOB_TASK: Final = f"{MAINTENANCE_NAMESPACE}.sweep_stalled_ingestion_job"
"""Un job `embedding`/`indexing` orphelin, dont la chaîne de sondage a disparu."""

RESUME_EXPIRED_PRECHECK_DECISION_TASK: Final = (
    f"{MAINTENANCE_NAMESPACE}.resume_expired_precheck_decision"
)
"""Un job `awaiting_review` sans décision humaine au-delà du délai configuré."""

TASK_MODULES: Final = (
    "app.workers.tasks.ingestion",
    "app.workers.tasks.evaluations",
    "app.workers.tasks.maintenance",
)
"""Modules du cœur importés par le worker au démarrage pour peupler le registre ;
ceux des greffons s'y ajoutent (`all_task_modules`)."""

TASK_ROUTES: Final[dict[str, dict[str, str]]] = {
    PRECHECK_TASK: {"queue": DEFAULT_QUEUE},
    CHUNKING_TASK: {"queue": DEFAULT_QUEUE},
    EMBEDDING_TASK: {"queue": DEFAULT_QUEUE},
    INDEXING_TASK: {"queue": DEFAULT_QUEUE},
    # Aucun appel GPU : réseau vers RAGFlow (échantillonnage, retrieval) et vers le
    # fournisseur LLM (génération, jugement) — même profil matériel que le pipeline
    # d'ingestion ci-dessus (ADR-013).
    DISPATCH_SCHEDULED_EVALUATIONS_TASK: {"queue": DEFAULT_QUEUE},
    RUN_SCHEDULED_EVALUATION_TASK: {"queue": DEFAULT_QUEUE},
    RUN_ASYNC_EVALUATION_TASK: {"queue": DEFAULT_QUEUE},
    # Sélection SQL et appels HTTP moteur/stockage, jamais de GPU (ADR-055).
    RUN_MAINTENANCE_SWEEP_TASK: {"queue": DEFAULT_QUEUE},
    SWEEP_STALLED_INGESTION_JOB_TASK: {"queue": DEFAULT_QUEUE},
    RESUME_EXPIRED_PRECHECK_DECISION_TASK: {"queue": DEFAULT_QUEUE},
}
"""Source de vérité du routage du cœur — cf. l'en-tête du module. Les greffons
déclarent le leur (`Plugin.task_routes`), fusionné par `all_task_routes`."""


def all_task_modules() -> tuple[str, ...]:
    """Modules de tâches du cœur, puis ceux des greffons."""
    return (*TASK_MODULES, *extra_task_modules())


def all_task_routes() -> dict[str, dict[str, str]]:
    """Routage complet : celui du cœur, puis celui des greffons."""
    return {**TASK_ROUTES, **extra_task_routes()}


RETRY_TASK_BY_SOURCE_STATE: Final[Mapping[JobState, str]] = {
    JobState.QUEUED: PRECHECK_TASK,
    JobState.AWAITING_REVIEW: CHUNKING_TASK,
    JobState.CHUNKING: EMBEDDING_TASK,
    JobState.EMBEDDING: INDEXING_TASK,
}
"""Tâche à republier pour reprendre un job `failed` (`POST /api/v1/jobs/{id}/retry`,
ADR-020).

Chaque clé est le `source` d'une étape du pipeline (`app.workers.tasks.ingestion`,
`PIPELINE_STEP_SOURCES`) — l'état vers lequel `JobError.retry_state` fait
retransiter le job avant republication de la tâche associée. Vit ici plutôt
que dans `app.workers.tasks.ingestion` : `app.services.jobs` (qui l'utilise
pour la reprise) ne peut pas importer ce module de tâches sans créer un cycle
(`app.workers.tasks.ingestion` importe déjà `IngestionJobService`). **Doit
rester synchronisé avec les quatre `Step.source`** — un test dédié
(`tests/domain/test_state_machine.py`,
`test_retry_source_states_are_exactly_the_pipeline_step_sources`) vérifie cette
correspondance pour qu'un futur ajout d'étape ne puisse pas diverger en
silence.
"""

CPU_SOFT_TIME_LIMIT: Final = 5 * 60
"""Une étape CPU qui dépasse 5 min est anormale : mieux vaut la couper tôt."""

CPU_TIME_LIMIT: Final = CPU_SOFT_TIME_LIMIT + 60
"""Marge d'une minute : la tâche a le temps de journaliser sa mort douce."""

GPU_SOFT_TIME_LIMIT: Final = 30 * 60
"""Une inférence est légitimement plus lente qu'un parsing — réservé à la Phase 5.

Aucune tâche ne porte encore ce profil (ADR-013) ; il continue néanmoins de
borner `BROKER_VISIBILITY_TIMEOUT`, dont l'invariant doit tenir dès qu'une
première tâche `gpu` existera.
"""

GPU_TIME_LIMIT: Final = GPU_SOFT_TIME_LIMIT + 60

BROKER_VISIBILITY_TIMEOUT: Final = 3600
"""Délai de redistribution d'un message non confirmé, côté Redis.

**Invariant à tenir** : il doit rester supérieur à `GPU_TIME_LIMIT` et au plus
grand `retry_backoff_max`, faute de quoi Redis redistribuerait une tâche
toujours en cours d'exécution — deux workers traiteraient alors le même job.
"""

MAX_RETRIES: Final = 5
"""Borne stricte : pas de retry infini sur une panne qui ne se répare pas seule."""

RETRY_BACKOFF_MAX_SECONDS: Final = 300
"""Plafond du backoff exponentiel (1 s, 2 s, 4 s… puis 5 min au maximum)."""

_RETRY_POLICY: Final[dict[str, object]] = {
    "autoretry_for": (Exception,),
    # Erreurs qu'une nouvelle tentative ne répare jamais : identifiant mal formé
    # (`ValueError`), job disparu (`NotFoundError`), transition illégale
    # (`ConflictError`, normalement impossible grâce à la garde d'idempotence).
    # Les retenter brûlerait cinq créneaux de worker pour rien.
    "dont_autoretry_for": (ConflictError, NotFoundError, ValueError),
    "max_retries": MAX_RETRIES,
    "retry_backoff": True,
    "retry_backoff_max": RETRY_BACKOFF_MAX_SECONDS,
    "retry_jitter": True,
}

CPU_TASK_OPTIONS: Final[dict[str, object]] = {
    **_RETRY_POLICY,
    "soft_time_limit": CPU_SOFT_TIME_LIMIT,
    "time_limit": CPU_TIME_LIMIT,
}
"""Options des tâches routées sur `default` — les quatre étapes du pipeline (ADR-013)."""

GPU_TASK_OPTIONS: Final[dict[str, object]] = {
    **_RETRY_POLICY,
    "soft_time_limit": GPU_SOFT_TIME_LIMIT,
    "time_limit": GPU_TIME_LIMIT,
}
"""Options des futures tâches routées sur `gpu` : mêmes retries, délais plus généreux.

Sans porteur aujourd'hui (ADR-013), comme `GPU_QUEUE` : la Phase 5 les prendra
telles quelles pour le reranking local.
"""


class Task[**P, R](Protocol):
    """Surface typée d'une tâche Celery, telle que le code applicatif l'utilise.

    Celery n'expose pas d'annotations (`py.typed` absent) : sans ce protocole,
    mypy strict verrait un décorateur non typé et rendrait `Any` chacune des
    quatre tâches. La signature de la fonction décorée est ici conservée, y
    compris sur `delay`. Les objets renvoyés par le transport (`AsyncResult`,
    `Signature`) restent des `object` : le projet ne les consomme pas.
    """

    name: str

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        """Exécute la tâche **en direct**, dans le process appelant."""
        ...

    def delay(self, *args: P.args, **kwargs: P.kwargs) -> object:
        """Publie la tâche sur sa file et renvoie son `AsyncResult`."""
        ...

    def apply_async(self, args: tuple[object, ...] | None = None, **options: object) -> object:
        """Publie la tâche avec des options de transport explicites."""
        ...

    def si(self, *args: P.args, **kwargs: P.kwargs) -> object:
        """Signature immuable, pour composer un `chain`/`group`."""
        ...


class CeleryConf(Protocol):
    """Configuration Celery : mise à jour groupée et accès par attribut.

    `__getattr__`/`__setattr__` reflètent la réalité de `celery.app.Settings`,
    dont les clés sont ouvertes — les nommer une à une n'apporterait rien.
    """

    def update(self, **settings: object) -> None:
        """Applique un lot de réglages."""
        ...

    def __getattr__(self, name: str) -> object: ...

    def __setattr__(self, name: str, value: object) -> None: ...


class CeleryApp(Protocol):
    """Surface de l'application Celery réellement utilisée par RAGEngine."""

    conf: CeleryConf
    tasks: Mapping[str, object]

    def task(self, **options: object) -> Callable[[Callable[..., object]], object]:
        """Enregistre une fonction dans le registre de tâches."""
        ...


def _build_app() -> CeleryApp:
    """Construit l'application Celery à partir de `Settings`."""
    settings = get_settings()
    celery = cast(
        "CeleryApp",
        Celery(
            "ragengine",
            broker=settings.celery_broker_url,
            backend=settings.celery_result_backend,
            include=all_task_modules(),
        ),
    )
    celery.conf.update(
        task_routes=all_task_routes(),
        task_default_queue=DEFAULT_QUEUE,
        # --- Sérialisation : JSON seul, jamais pickle (exécution de code arbitraire).
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # --- Reprise après crash -------------------------------------------------
        # Le message n'est confirmé qu'**après** exécution : un worker tué en
        # plein traitement laisse sa tâche redistribuable au lieu de la perdre.
        # C'est la condition de « un kill du worker reprend proprement », et elle
        # n'est tenable que parce que chaque tâche est idempotente
        # (`app.workers.tasks.ingestion`, garde d'état).
        task_acks_late=True,
        # Sans ceci, un worker tué par SIGKILL/OOM voit sa tâche marquée en échec
        # sans être remise en file : `acks_late` seul ne suffit pas.
        task_reject_on_worker_lost=True,
        # Un seul message réservé d'avance par process : recommandé avec
        # `acks_late`, et indispensable pour les sondages replanifiés, dont un
        # message en attente dans la mémoire d'un worker est un message que
        # personne d'autre ne prend.
        worker_prefetch_multiplier=1,
        broker_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT},
        result_backend_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT},
        # Le broker n'est pas forcément prêt avant le worker (`depends_on` ne
        # garantit que l'ordre de démarrage, pas la disponibilité applicative).
        broker_connection_retry_on_startup=True,
        # --- Délais d'exécution par défaut : profil CPU (le plus court) ----------
        # Une future tâche `gpu` relèvera ces deux bornes via `GPU_TASK_OPTIONS`.
        task_soft_time_limit=CPU_SOFT_TIME_LIMIT,
        task_time_limit=CPU_TIME_LIMIT,
        task_track_started=True,
        result_expires=24 * 3600,
        # --- Celery Beat : routine d'évaluation programmée (Phase 12 Étape 4) -----
        # Un seul point d'entrée périodique, qui fait elle-même le fan-out par base
        # éligible (`app.workers.tasks.evaluations.dispatch_scheduled_evaluations`) :
        # ajouter une future routine programmée n'est qu'une entrée de plus ici,
        # jamais un second processus `beat`.
        beat_schedule={
            "evaluation-scheduled-dispatch": {
                "task": DISPATCH_SCHEDULED_EVALUATIONS_TASK,
                "schedule": timedelta(hours=settings.evaluation_scheduled_interval_hours),
            },
            "maintenance-sweep": {
                "task": RUN_MAINTENANCE_SWEEP_TASK,
                "schedule": timedelta(minutes=settings.maintenance_sweep_interval_minutes),
            },
        },
    )
    return celery


app = _build_app()
"""Instance résolue par `celery -A app.workers.celery_app` — **ne pas renommer**."""


def task[**P, R](*, name: str, **options: object) -> Callable[[Callable[P, R]], Task[P, R]]:
    """Enregistre une fonction comme tâche Celery, en gardant son typage.

    Enveloppe `app.task` — non typée — derrière `Task[P, R]`. Aucun `queue=`
    n'est accepté : le routage se déclare dans `TASK_ROUTES`, et nulle part
    ailleurs.

    Args:
        name: nom global de la tâche, tel qu'il figure dans `TASK_ROUTES`.
        **options: options Celery (retry, délais) — voir `CPU_TASK_OPTIONS`.
    """

    def decorate(func: Callable[P, R]) -> Task[P, R]:
        return cast("Task[P, R]", app.task(name=name, **options)(func))

    return decorate


def _ensure_task_registry() -> None:
    """Importe les modules de tâches (cœur et greffons) à la demande, pour peupler le registre.

    Un **producteur** — la façade FastAPI, qui publie sans jamais exécuter —
    n'importe pas le module des tâches : seul le worker le charge au démarrage.
    Sans cet import différé, publier depuis l'API échouerait sur `NotRegistered`
    alors que publier ne demande qu'un nom et une route. L'import est fait ici,
    à l'appel, et non en tête de module : le faire à l'import créerait un cycle
    avec les services que les tâches consomment.
    """
    for module in all_task_modules():
        import_module(module)


def task_by_name(name: str) -> Task[..., object]:
    """Résout une tâche enregistrée par son nom.

    Sert le chaînage d'une étape vers la suivante sans import circulaire ni
    ordre de déclaration imposé : `run_chunking` peut publier `run_embedding`
    alors même qu'elle est définie plus haut dans le module. Sert aussi à l'API,
    qui réveille le pipeline après la décision de precheck.
    """
    if name not in app.tasks:
        _ensure_task_registry()
    return cast("Task[..., object]", app.tasks[name])


def _configure_worker_logging(**kwargs: object) -> None:
    """Impose le logging JSON du projet au worker (signal `setup_logging`).

    Se brancher sur ce signal **désactive** la configuration de logging propre à
    Celery : `RAGENGINE_LOG_LEVEL` devient la seule source du niveau, et
    `--loglevel` de la ligne de commande n'a plus d'effet. C'est le prix d'une
    sortie strictement homogène entre l'API et les workers (CONTRIBUTING.md §6).
    """
    del kwargs
    configure_logging(get_settings().log_level)


def _dispose_worker_engine(**kwargs: object) -> None:
    """Ferme le pool PostgreSQL à l'arrêt du worker (signal `worker_shutdown`)."""
    del kwargs
    dispose_engine()
    _logger.info("Moteur PostgreSQL du worker fermé")


# `connect` en appel, pas en décorateur : un décorateur non typé rendrait la
# fonction qu'il décore non typée aux yeux de mypy strict.
setup_logging.connect(_configure_worker_logging)
worker_shutdown.connect(_dispose_worker_engine)
