"""Routine d'évaluation programmée (Phase 12, Étape 4) — deux tâches, Celery Beat.

`EvaluationService.create_run` (Phase 8, `app.services.evaluations`) existait
déjà, mais restait un geste manuel : un appel HTTP explicite depuis l'UI,
borné à `MAX_EVALUATION_SAMPLE_SIZE` pour rester compatible avec la latence
d'une requête. Ce module l'exécute désormais **sans intervention humaine**,
depuis une tâche Celery (donc sans la contrainte de latence HTTP), pour
chaque base `evaluation_enabled=true` — le même flag que celui déjà exposé
par `KnowledgeBase`, pas un second mécanisme d'éligibilité.

**Deux tâches, un patron « dispatch + fan-out ».**

`dispatch_scheduled_evaluations` — publiée par `beat_schedule`
(`app.workers.celery_app`, une fois par `Settings.
evaluation_scheduled_interval_hours`, 24h par défaut) — liste les bases
éligibles et publie `run_scheduled_evaluation` pour chacune. Elle ne fait
elle-même **aucun** appel LLM ni moteur : une base ne doit jamais pouvoir
empêcher le dispatch des suivantes, et publier des tâches indépendantes le
garantit par construction (chaque `.delay()` est une publication atomique,
sans rapport avec le sort des autres) — bien plus robuste qu'une boucle
`await` unique dans un seul process, qui s'arrêterait à la première exception
non rattrapée.

`run_scheduled_evaluation` — une tâche par base, sur la file `default` :
aucun appel n'y est GPU, seulement du réseau vers RAGFlow (échantillonnage,
retrieval) et vers le fournisseur LLM (génération, jugement), même profil
matériel que les quatre étapes du pipeline d'ingestion (ADR-013). Elle rejoue
`EvaluationService.create_run` avec :

- le principal du **propriétaire** de la base : aucun utilisateur humain ne
  déclenche cette action, mais `Principal` n'a pas de notion de « système » et
  `RetrievalService.search`/`chat` (appelés en interne par `create_run`)
  exigent un principal disposant d'au moins `MemberRole.VIEWER` sur la base.
  Le propriétaire est le choix le moins arbitraire : c'est lui qui a activé
  `evaluation_enabled`, donc la personne accountable pour ce coût LLM
  récurrent. Conséquence assumée : `EvaluationRun.requested_by_id` d'un run
  programmé porte l'identifiant du propriétaire, exactement comme s'il avait
  cliqué lui-même — rien ne distingue aujourd'hui un run programmé d'un run
  manuel dans l'historique (`GET .../evaluations`), et ça n'a pas semblé
  justifier une colonne dédiée pour cette étape ;
- `Settings.evaluation_scheduled_sample_size` (8 par défaut) et
  `Settings.evaluation_scheduled_llm_provider_id` (Groq
  `llama-3.3-70b-versatile` par défaut — **jamais** Anthropic, dont le crédit
  peut s'épuiser indépendamment, CONTRIBUTING.md §2) à la place des valeurs saisies
  par un appelant HTTP.

**Dépense LLM (ADR-054).** La routine ne contourne aucun compteur : `create_run`
impute chaque appel au compte du propriétaire puis au budget global (hors clé
personnelle), exactement comme un run manuel. Le dispatch écarte en plus les
bases dont le propriétaire est un compte **éphémère** (`is_guest` :
l'évaluation lui est refusée, une base d'essai jetable ne doit pas coûter un run
par jour) ou un compte **suspendu** — le service refuserait l'invité de toute
façon, le filtre évite de publier une tâche vouée à l'échec.

**Robustesse budget/disjoncteur.** Toute exception levée par `create_run` —
disjoncteur ouvert (`LLMCircuitOpenError`), fournisseur à sec ou en erreur
(`LLMError` et ses sous-classes), base sans document éligible
(`ConflictError`), fournisseur inconnu ou mal configuré (`ValidationError`),
ou toute autre panne transitoire — est journalisée avec un repère de log
stable (`SCHEDULED_EVALUATION_FAILURE_MARKER`, même conception que
`DEAD_LETTER_MARKER` de `app.workers.tasks.ingestion`) puis **avalée** : la
tâche se termine sans exception, donc sans jamais déclencher la politique de
retry Celery (`CPU_TASK_OPTIONS.autoretry_for`) — un fournisseur à sec le
reste au retry suivant, le retenter immédiatement ne ferait qu'aggraver le
problème de budget déjà identifié ; la seule reprise légitime est le prochain
déclenchement programmé (le lendemain, par défaut). Aucun état n'est écrit en
base pour un run qui n'a jamais été créé : `EvaluationRun` ne porte pas de
colonne de statut (Phase 8, et `create_run` ne persiste le run qu'une fois
tous ses items produits) — lui en ajouter une pour ce seul cas de figure
aurait été disproportionné. Le journal structuré est donc la source de
vérité opérationnelle pour un échec de routine, exactement comme
`_dead_letter` l'est déjà pour un job d'ingestion en échec définitif.
"""

from __future__ import annotations

import logging
import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.db.models import EvaluationRun, KnowledgeBase, User
from app.domain.enums import EvaluationRunStatus
from app.domain.schemas.evaluation import EvaluationCreate, EvaluationRunRead
from app.domain.schemas.principal import AuthMethod
from app.services.evaluations import EvaluationService
from app.services.identity.principal import Principal
from app.workers.celery_app import (
    CPU_TASK_OPTIONS,
    DISPATCH_SCHEDULED_EVALUATIONS_TASK,
    RUN_ASYNC_EVALUATION_TASK,
    RUN_SCHEDULED_EVALUATION_TASK,
    task,
)
from app.workers.session import run_in_session

_logger = logging.getLogger(__name__)

SCHEDULED_EVALUATION_FAILURE_MARKER: Final = "evaluation.scheduled_run_failed"
"""Valeur de `extra={"event": ...}` distinguant un échec de routine programmée de
tout autre log d'erreur — même conception que `DEAD_LETTER_MARKER`
(`app.workers.tasks.ingestion`) : le point d'accroche d'une future règle
d'alerte côté observabilité, sans mécanisme Celery supplémentaire."""

ASYNC_EVALUATION_FAILURE_MARKER: Final = "evaluation.async_run_failed"
"""Même rôle que `SCHEDULED_EVALUATION_FAILURE_MARKER`, pour le run **asynchrone
déclenché par un utilisateur** (Phase 12 Étape 4, item restant, ADR-028) —
distinct parce que ce chemin, contrairement à la routine programmée, laisse
une trace visible côté produit : le run existe déjà en base (`pending`) avant
l'exécution, et `EvaluationService.run_pending_evaluation` le fait déjà
transiter vers `failed` avant de relever l'exception que cette tâche
journalise ici puis avale — jamais de retry Celery agressif, même raison
budget/disjoncteur que la routine programmée."""

_MAX_LOGGED_REASON_LENGTH: Final = 500
"""Le journal reçoit un message lisible, pas une trace complète (même borne que
`MAX_ERROR_MESSAGE_LENGTH`, `app.workers.tasks.ingestion`)."""


def _service(db: AsyncSession, settings: Settings) -> EvaluationService:
    """Construit le service — point d'injection pour les tests.

    Même rôle que `_engine()`/`_storage()` dans `app.workers.tasks.ingestion` :
    un test monkeypatche cette fonction pour substituer un moteur et un
    fournisseur LLM factices, sans toucher au corps des tâches.
    """
    return EvaluationService(db, settings)


def _truncated_reason(message: str) -> str:
    if len(message) <= _MAX_LOGGED_REASON_LENGTH:
        return message
    return f"{message[: _MAX_LOGGED_REASON_LENGTH - 1]}…"


async def _eligible_knowledge_base_ids(db: AsyncSession) -> list[uuid.UUID]:
    """Bases `evaluation_enabled=true` dont le propriétaire est un compte réel actif.

    Même flag que celui exposé par l'UI ; le filtre sur le propriétaire (ni
    invité, ni suspendu) ferme le fan-out d'une routine quotidienne sur des
    bases que n'importe qui peut ouvrir (ADR-054).

    Ordonné par `created_at` : ordre stable et reproductible du dispatch d'un
    cycle à l'autre — utile en lecture de logs, et à la reproductibilité d'un
    test qui scénarise plusieurs bases dans le même batch.
    """
    statement = (
        select(KnowledgeBase.id)
        .join(User, User.id == KnowledgeBase.owner_id)
        .where(
            KnowledgeBase.evaluation_enabled.is_(True),
            User.is_guest.is_(False),
            User.is_active.is_(True),
        )
        .order_by(KnowledgeBase.created_at)
    )
    return list((await db.execute(statement)).scalars().all())


async def _run_for_knowledge_base(
    db: AsyncSession, kb_id: uuid.UUID, settings: Settings
) -> EvaluationRunRead | None:
    """Charge la base et déclenche un run — `None` si la base n'est plus éligible.

    Une base peut avoir été supprimée ou désactivée entre le dispatch (lecture
    de `_eligible_knowledge_base_ids`) et l'exécution de cette tâche : ni l'un
    ni l'autre n'est une erreur, c'est la même course bénigne que la garde
    d'idempotence du pipeline d'ingestion (`app.workers.tasks.ingestion`,
    `Outcome.SKIPPED`) — la tâche se termine proprement, sans exception.
    """
    knowledge_base = await db.get(KnowledgeBase, kb_id, options=[selectinload(KnowledgeBase.owner)])
    if (
        knowledge_base is None
        or not knowledge_base.evaluation_enabled
        or knowledge_base.owner.is_guest
        or not knowledge_base.owner.is_active
    ):
        _logger.info(
            "Run d'évaluation programmé ignoré : base absente, évaluation désactivée, ou "
            "propriétaire invité ou suspendu",
            extra={"knowledge_base_id": str(kb_id)},
        )
        return None

    principal = Principal(auth_method=AuthMethod.SESSION, user=knowledge_base.owner)
    payload = EvaluationCreate(
        sample_size=settings.evaluation_scheduled_sample_size,
        llm_provider_id=settings.evaluation_scheduled_llm_provider_id,
    )
    return await _service(db, settings).create_run(principal, knowledge_base, payload)


@task(name=RUN_SCHEDULED_EVALUATION_TASK, **CPU_TASK_OPTIONS)
def run_scheduled_evaluation(knowledge_base_id: str) -> str:
    """Un run d'évaluation programmé pour une base. Ne relève jamais d'exception à Celery.

    Args:
        knowledge_base_id: identifiant de la base, sérialisé en `str` (JSON
            seul, jamais pickle — même convention que `job_id` dans
            `app.workers.tasks.ingestion`).

    Returns:
        `"completed"` (y compris si la base n'était plus éligible : rien à
        faire n'est un succès, pas un échec) ou `"failed"` si `create_run` a
        levé — jamais l'exception elle-même, cf. la docstring du module.
    """
    settings = get_settings()
    kb_id = uuid.UUID(knowledge_base_id)
    try:
        run_in_session(lambda db: _run_for_knowledge_base(db, kb_id, settings))
    except Exception as error:  # avalé délibérément, cf. docstring du module.
        _logger.error(
            "Run d'évaluation programmé en échec — routine préservée pour les bases suivantes",
            extra={
                "event": SCHEDULED_EVALUATION_FAILURE_MARKER,
                "knowledge_base_id": knowledge_base_id,
                "error_type": type(error).__name__,
                "reason": _truncated_reason(str(error)),
            },
        )
        return "failed"
    return "completed"


async def _run_pending_evaluation(
    db: AsyncSession, kb_id: uuid.UUID, run_id: uuid.UUID, settings: Settings
) -> str:
    """Charge la base, le run et son auteur, puis délègue à `run_pending_evaluation`.

    Reconstruit un `Principal` depuis `EvaluationRun.requested_by_id` — jamais
    depuis `evaluation_enabled`/le propriétaire de la base comme le fait
    `_run_for_knowledge_base` : ce run a été demandé par un utilisateur
    identifié, encore membre au moins `VIEWER` au moment de la requête HTTP
    (`require_kb_permission`), et c'est ce compte-là, pas nécessairement le
    propriétaire, que `RetrievalService.search`/`chat` doivent voir.
    """
    knowledge_base = await db.get(KnowledgeBase, kb_id)
    run = await db.get(EvaluationRun, run_id)
    if knowledge_base is None or run is None:
        _logger.info(
            "Run d'évaluation asynchrone ignoré : base ou run introuvable",
            extra={"knowledge_base_id": str(kb_id), "evaluation_run_id": str(run_id)},
        )
        return "skipped"

    requester = await db.get(User, run.requested_by_id) if run.requested_by_id else None
    if requester is None:
        # Défensif, en pratique jamais atteint : `POST .../evaluations` exige
        # `MemberRole.EDITOR`, donc un principal authentifié dont l'identifiant
        # a été capturé sur `requested_by_id` à la création du run. Sans
        # compte à reconstruire, `RetrievalService` ne peut vérifier aucun
        # accès : le run est marqué en échec plutôt que de tenter un appel
        # qui échouerait de toute façon plus loin, avec une trace moins claire.
        run.status = EvaluationRunStatus.FAILED
        await db.flush([run])
        await db.commit()
        _logger.error(
            "Run d'évaluation asynchrone sans auteur reconstructible",
            extra={
                "event": ASYNC_EVALUATION_FAILURE_MARKER,
                "knowledge_base_id": str(kb_id),
                "evaluation_run_id": str(run_id),
                "error_type": "MissingRequester",
                "reason": "requested_by_id absent ou compte supprimé.",
            },
        )
        return "failed"

    principal = Principal(auth_method=AuthMethod.SESSION, user=requester)
    await _service(db, settings).run_pending_evaluation(principal, knowledge_base, run_id)
    return "completed"


@task(name=RUN_ASYNC_EVALUATION_TASK, **CPU_TASK_OPTIONS)
def run_async_evaluation(knowledge_base_id: str, evaluation_run_id: str) -> str:
    """Exécute un run d'évaluation asynchrone déjà persisté `pending`.

    Publiée par `EvaluationService.create_pending_run` (`POST .../evaluations`,
    `sample_size` au-delà du plafond synchrone, ADR-028) — sœur de
    `run_scheduled_evaluation`, jamais une généralisation de celle-ci :
    `sample_size`/`llm_provider_id` sont déjà portés par le run persisté
    (`run.sample_size`, `run.llm_provider_id`), pas résolus ici depuis
    `Settings`. Ne relève jamais d'exception à Celery, même politique que
    `run_scheduled_evaluation` : un fournisseur à sec ou un disjoncteur ouvert
    ne se répare pas par un retry immédiat, et `run_pending_evaluation` a déjà
    fait transiter le run vers `failed`, de façon visible pour l'utilisateur
    qui l'a déclenché (`GET .../evaluations/{id}`) — contrairement à la
    routine programmée, ce chemin n'a pas besoin du journal comme seule
    source de vérité, le run lui-même en porte une.

    Args:
        knowledge_base_id: identifiant de la base, sérialisé en `str`.
        evaluation_run_id: identifiant du run déjà `pending`, sérialisé en `str`.

    Returns:
        `"completed"`, `"skipped"` (base ou run disparu — course bénigne) ou
        `"failed"`.
    """
    settings = get_settings()
    kb_id = uuid.UUID(knowledge_base_id)
    run_id = uuid.UUID(evaluation_run_id)
    try:
        return run_in_session(lambda db: _run_pending_evaluation(db, kb_id, run_id, settings))
    except Exception as error:  # avalé délibérément, cf. docstring de la fonction.
        _logger.error(
            "Run d'évaluation asynchrone en échec",
            extra={
                "event": ASYNC_EVALUATION_FAILURE_MARKER,
                "knowledge_base_id": knowledge_base_id,
                "evaluation_run_id": evaluation_run_id,
                "error_type": type(error).__name__,
                "reason": _truncated_reason(str(error)),
            },
        )
        return "failed"


@task(name=DISPATCH_SCHEDULED_EVALUATIONS_TASK, **CPU_TASK_OPTIONS)
def dispatch_scheduled_evaluations() -> int:
    """Publie un `run_scheduled_evaluation` par base éligible — planifiée par `beat_schedule`.

    Returns:
        Le nombre de bases visées, pour lecture dans Flower/les logs — jamais
        consommé par du code applicatif.
    """
    kb_ids = run_in_session(_eligible_knowledge_base_ids)
    for kb_id in kb_ids:
        run_scheduled_evaluation.delay(str(kb_id))
    _logger.info(
        "Dispatch des runs d'évaluation programmés",
        extra={"knowledge_base_count": len(kb_ids)},
    )
    return len(kb_ids)
