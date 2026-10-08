"""Routage des files et politiques d'exécution de l'application Celery.

Ce que ces tests verrouillent : l'anti-OOM de CONTRIBUTING.md §2 est une propriété
**de construction**, lisible dans la configuration de l'app, sans qu'aucun
worker ni aucun Redis ne tourne. Ils échouent donc dès qu'une étape change de
matériel par inadvertance.
"""

from __future__ import annotations

from datetime import timedelta
from importlib import import_module

import pytest

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.workers import celery_app as module
from app.workers.celery_app import (
    CHUNKING_TASK,
    DISPATCH_SCHEDULED_EVALUATIONS_TASK,
    EMBEDDING_TASK,
    INDEXING_TASK,
    PRECHECK_TASK,
    RESUME_EXPIRED_PRECHECK_DECISION_TASK,
    RUN_ASYNC_EVALUATION_TASK,
    RUN_MAINTENANCE_SWEEP_TASK,
    RUN_SCHEDULED_EVALUATION_TASK,
    SWEEP_STALLED_INGESTION_JOB_TASK,
    app,
)
from app.workers.tasks import evaluations, ingestion, maintenance

PIPELINE_TASKS = (PRECHECK_TASK, CHUNKING_TASK, EMBEDDING_TASK, INDEXING_TASK)
"""Les quatre étapes d'ingestion — toutes routées sur `default` depuis ADR-013."""

EVALUATION_TASKS = (
    DISPATCH_SCHEDULED_EVALUATIONS_TASK,
    RUN_SCHEDULED_EVALUATION_TASK,
    RUN_ASYNC_EVALUATION_TASK,
)
"""Les trois tâches d'évaluation hors-requête : routine programmée (Phase 12 Étape 4) et
run asynchrone à la demande (Phase 12 Étape 4, item restant, ADR-028)."""

MAINTENANCE_TASKS = (
    RUN_MAINTENANCE_SWEEP_TASK,
    SWEEP_STALLED_INGESTION_JOB_TASK,
    RESUME_EXPIRED_PRECHECK_DECISION_TASK,
)
"""Le balayage de maintenance et ses sous-tâches du cœur (ADR-055, Motif C) : sélection et
dispatch seulement, jamais de travail GPU."""


def resolved_queue(task_name: str) -> str:
    """File réellement choisie par le routeur Celery pour ce nom de tâche."""
    return str(app.amqp.router.route({}, task_name)["queue"].name)


@pytest.mark.parametrize("task_name", PIPELINE_TASKS)
def test_every_ingestion_step_is_routed_to_the_default_queue(task_name: str) -> None:
    """ADR-013 : aucune étape du pipeline ne fait de travail GPU dans son process.

    Le precheck est une analyse locale sans modèle ; le découpage, la
    vectorisation et l'indexation ne sont, côté RAGEngine, que des appels HTTP au
    moteur — c'est lui qui appelle le service d'inférence, de son côté. Les
    router sur `gpu` gaspillerait l'unique créneau de concurrence 1 sur du
    travail réseau.
    """
    assert module.TASK_ROUTES[task_name] == {"queue": module.DEFAULT_QUEUE}
    assert resolved_queue(task_name) == module.DEFAULT_QUEUE


def test_the_gpu_queue_carries_no_ingestion_task() -> None:
    """La file `gpu` reste entièrement libre pour la Phase 5 (reranking local)."""
    assert module.GPU_QUEUE not in {route["queue"] for route in module.TASK_ROUTES.values()}


def test_an_unrouted_task_falls_back_to_the_cpu_queue() -> None:
    """Un oubli dans la table coûte du CPU, jamais de la VRAM."""
    assert resolved_queue("ragengine.ingestion.tache_inexistante") == module.DEFAULT_QUEUE


def test_every_registered_task_is_routed() -> None:
    """Aucune tâche RAGEngine (pipeline, évaluation ou maintenance) ne circule sans affectation
    explicite."""
    namespaces = (
        module.INGESTION_NAMESPACE,
        module.EVALUATION_NAMESPACE,
        module.MAINTENANCE_NAMESPACE,
    )
    for task_module in module.all_task_modules():
        import_module(task_module)
    registered = {name for name in app.tasks if name.startswith(namespaces)}

    assert registered == set(module.all_task_routes())


def test_the_four_pipeline_steps_are_registered() -> None:
    """Le module de tâches est bien celui qu'importe le worker (`TASK_MODULES`)."""
    assert ingestion.__name__ in module.TASK_MODULES
    assert {ingestion.run_precheck.name, ingestion.run_chunking.name} == {
        PRECHECK_TASK,
        CHUNKING_TASK,
    }
    assert {ingestion.run_embedding.name, ingestion.run_indexing.name} == {
        EMBEDDING_TASK,
        INDEXING_TASK,
    }


def test_a_task_is_only_acknowledged_once_executed() -> None:
    """`acks_late` + `reject_on_worker_lost` : un worker tué rend sa tâche à la file."""
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1


def test_the_broker_never_redelivers_a_running_task() -> None:
    """Le délai de visibilité Redis doit couvrir la plus longue tâche possible."""
    assert module.BROKER_VISIBILITY_TIMEOUT > module.GPU_TIME_LIMIT
    assert module.BROKER_VISIBILITY_TIMEOUT > module.RETRY_BACKOFF_MAX_SECONDS
    assert app.conf.broker_transport_options["visibility_timeout"] == (
        module.BROKER_VISIBILITY_TIMEOUT
    )


def test_payloads_are_json_only() -> None:
    """Pas de pickle : un message de file ne doit jamais pouvoir exécuter du code."""
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]


@pytest.mark.parametrize("task_name", PIPELINE_TASKS)
def test_retries_are_bounded_and_backed_off(task_name: str) -> None:
    """Backoff exponentiel plafonné, nombre de tentatives borné."""
    task = app.tasks[task_name]

    assert task.max_retries == module.MAX_RETRIES
    assert task.retry_backoff is True
    assert task.retry_backoff_max == module.RETRY_BACKOFF_MAX_SECONDS
    assert task.autoretry_for == (Exception,)


@pytest.mark.parametrize("task_name", PIPELINE_TASKS)
def test_a_permanent_error_is_never_retried(task_name: str) -> None:
    """Un identifiant illisible, un job disparu ou une transition illégale ne se réparent pas."""
    assert app.tasks[task_name].dont_autoretry_for == (ConflictError, NotFoundError, ValueError)


@pytest.mark.parametrize("task_name", PIPELINE_TASKS)
def test_every_ingestion_step_carries_the_cpu_budget(task_name: str) -> None:
    """Un appel HTTP ou une analyse légère n'a pas droit au budget d'une inférence."""
    step = app.tasks[task_name]

    assert step.soft_time_limit == module.CPU_SOFT_TIME_LIMIT
    assert step.time_limit == module.CPU_TIME_LIMIT
    assert module.CPU_SOFT_TIME_LIMIT < module.GPU_SOFT_TIME_LIMIT


def test_the_default_time_limit_is_the_cpu_profile() -> None:
    """Les bornes globales sont les plus courtes ; une tâche `gpu` les relèvera un jour."""
    assert app.conf.task_soft_time_limit == module.CPU_SOFT_TIME_LIMIT
    assert app.conf.task_time_limit == module.CPU_TIME_LIMIT
    assert app.conf.task_soft_time_limit < app.conf.task_time_limit


# ─── Routine d'évaluation programmée (Phase 12 Étape 4) ─────────────────────


@pytest.mark.parametrize("task_name", EVALUATION_TASKS)
def test_every_evaluation_task_is_routed_to_the_default_queue(task_name: str) -> None:
    """Aucun appel n'y est GPU : réseau vers RAGFlow et le fournisseur LLM (ADR-013)."""
    assert module.TASK_ROUTES[task_name] == {"queue": module.DEFAULT_QUEUE}
    assert resolved_queue(task_name) == module.DEFAULT_QUEUE


def test_the_evaluation_task_module_is_registered() -> None:
    """Le module de tâches est bien celui qu'importe le worker (`TASK_MODULES`)."""
    assert evaluations.__name__ in module.TASK_MODULES
    assert evaluations.dispatch_scheduled_evaluations.name == DISPATCH_SCHEDULED_EVALUATIONS_TASK
    assert evaluations.run_scheduled_evaluation.name == RUN_SCHEDULED_EVALUATION_TASK
    assert evaluations.run_async_evaluation.name == RUN_ASYNC_EVALUATION_TASK


# ─── Balayage de maintenance (ADR-055, Motif C) ─────────────────────────────


@pytest.mark.parametrize("task_name", MAINTENANCE_TASKS)
def test_every_maintenance_task_is_routed_to_the_default_queue(task_name: str) -> None:
    """Sélection SQL et appels moteur/stockage, jamais de GPU (CONTRIBUTING.md §2)."""
    assert module.TASK_ROUTES[task_name] == {"queue": module.DEFAULT_QUEUE}
    assert resolved_queue(task_name) == module.DEFAULT_QUEUE


def test_the_maintenance_task_module_is_registered() -> None:
    """Le module de tâches est bien celui qu'importe le worker (`TASK_MODULES`)."""
    assert maintenance.__name__ in module.TASK_MODULES
    assert maintenance.run_maintenance_sweep.name == RUN_MAINTENANCE_SWEEP_TASK
    assert maintenance.sweep_stalled_ingestion_job.name == SWEEP_STALLED_INGESTION_JOB_TASK
    assert (
        maintenance.resume_expired_precheck_decision.name == RESUME_EXPIRED_PRECHECK_DECISION_TASK
    )


def test_the_maintenance_sweep_is_scheduled_by_celery_beat() -> None:
    """Deuxième entrée de `beat_schedule` dans ce dépôt, après la routine d'évaluation."""
    entry = app.conf.beat_schedule["maintenance-sweep"]

    assert entry["task"] == RUN_MAINTENANCE_SWEEP_TASK


def test_the_maintenance_sweep_default_schedule_is_documented() -> None:
    """Fréquence par défaut documentée (`Settings.maintenance_sweep_interval_minutes`)."""
    settings = get_settings()
    entry = app.conf.beat_schedule["maintenance-sweep"]

    assert settings.maintenance_sweep_interval_minutes == 15
    assert entry["schedule"] == timedelta(minutes=settings.maintenance_sweep_interval_minutes)


def test_the_dispatch_task_is_scheduled_by_celery_beat() -> None:
    """Premier usage de `beat_schedule` dans ce dépôt (Phase 12 Étape 4)."""
    entry = app.conf.beat_schedule["evaluation-scheduled-dispatch"]

    assert entry["task"] == DISPATCH_SCHEDULED_EVALUATIONS_TASK


def test_the_default_schedule_is_once_a_day() -> None:
    """Fréquence par défaut documentée (`Settings.evaluation_scheduled_interval_hours`)."""
    settings = get_settings()
    entry = app.conf.beat_schedule["evaluation-scheduled-dispatch"]

    assert settings.evaluation_scheduled_interval_hours == 24
    assert entry["schedule"] == timedelta(hours=settings.evaluation_scheduled_interval_hours)
