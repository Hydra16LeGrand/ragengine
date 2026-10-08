"""Génération d'évaluation (Phase 8) : `POST/GET .../evaluations[/{evaluation_id}]`.

Toujours nichés sous `{kb_id}` (jamais de variante racine comme pour le
retrieval, cf. `app/api/v1/routes/retrieval.py`) : `require_kb_permission`
s'y monte directement, exactement comme dans `documents.py`. Le routeur ne
fait que valider, déléguer à `EvaluationService` et sérialiser — aucune
logique métier ici (CONTRIBUTING.md §3/§4).

**RBAC** : lancer une évaluation consomme un budget LLM réel (génération +
jugement, jusqu'à deux appels par document échantillonné, plus un aller-retour
retrieval) — décision de session Phase 8, documentée ici faute d'ADR dédiée :
alignée sur `MemberRole.EDITOR`, un cran au-dessus de `/retrieval/search` et
`/retrieval/chat` (`VIEWER`), qui ne coûtent rien de plus qu'une requête
ponctuelle. Consulter l'historique et le détail d'un run reste en lecture
(`VIEWER`), symétrique du reste de l'API.

Pas de pagination sur `GET .../evaluations` (écart assumé par rapport à
`GET .../documents`) : un run est une action manuelle et coûteuse, son volume
par base reste sans commune mesure avec les documents ingérés en masse.

**Deux chemins d'exécution sous le même `POST`** (Phase 12 Étape 4, item
restant, ADR-028) : `sample_size ≤ MAX_EVALUATION_SAMPLE_SIZE` (20) reste
synchrone, comportement inchangé depuis la Phase 8 (`201`, run complet).
Au-delà, le routeur bascule vers `EvaluationService.create_pending_run` et
répond `202` avec un run `pending`, sans item — c'est la seule décision prise
ici, tout le reste (validation, plafond réel, publication Celery) vit dans le
service, comme partout ailleurs dans ce fichier.

`GET /evaluations/summary` (Phase 12, Étape 5) est **racine**, comme
`/retrieval/search` (cf. `app/api/v1/routes/retrieval.py`) — pas de `kb_id`
de chemin puisqu'il agrège justement *toutes* les bases visibles du
principal. `require_kb_permission` ne peut donc pas s'y monter ; la portée
RBAC est celle de `KnowledgeBaseService.list_visible`, résolue par
`app.services.evaluation_summary`.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from app.api.v1.dependencies import (
    CurrentPrincipal,
    SettingsDep,
    require_kb_permission,
    require_scope,
)
from app.db.session import DbSession
from app.domain.enums import MemberRole
from app.domain.schemas.evaluation import (
    MAX_EVALUATION_SAMPLE_SIZE,
    EvaluationCreate,
    EvaluationRunRead,
    EvaluationRunSummary,
)
from app.domain.schemas.evaluation_summary import EvaluationsSummaryRead
from app.domain.scopes import ApiKeyScope
from app.services.evaluation_summary import compute_visible_evaluation_summary
from app.services.evaluations import EvaluationService
from app.services.identity.permissions import KnowledgeBaseAccess

router = APIRouter(
    tags=["evaluations"],
    responses={
        int(HTTPStatus.NOT_FOUND): {
            "description": "Base ou évaluation inexistante, ou invisible pour le principal."
        }
    },
)

KbRead = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.VIEWER))]
"""Lecture de l'historique/détail : membre `viewer`, ou visiteur d'une base `public`."""

KbWrite = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.EDITOR))]
"""Lancement d'un run : `owner` ou `editor` — consomme un budget LLM réel."""

ScopeRead = Depends(require_scope(ApiKeyScope.EVALUATIONS_READ))
"""Portée de clé API exigée en lecture — sans effet hors authentification par clé."""

ScopeWrite = Depends(require_scope(ApiKeyScope.EVALUATIONS_WRITE))
"""Portée de clé API exigée pour lancer un run — consomme un budget LLM réel."""

EvaluationPath = Annotated[uuid.UUID, Path(description="Identifiant du run d'évaluation.")]


@router.post(
    "/knowledge-bases/{kb_id}/evaluations",
    status_code=HTTPStatus.CREATED,
    summary="Lancer une génération d'évaluation",
    description=(
        "Échantillonne `sample_size` documents indexés (défaut 5), génère une "
        "question/réponse de référence par document depuis ses fragments "
        "bruts, note chaque question (taux de rappel retrieval + score de "
        "juge LLM) et renvoie le run.\n\n"
        f"`sample_size ≤ {MAX_EVALUATION_SAMPLE_SIZE}` : **synchrone**, pas de "
        "job Celery, l'appel bloque jusqu'à la fin du run et répond `201` "
        "avec le run complet, items inclus.\n\n"
        f"`sample_size > {MAX_EVALUATION_SAMPLE_SIZE}` : **asynchrone** "
        '(ADR-028) — répond `202` avec un run `status: "pending"` sans '
        "item, exécuté par un job Celery (`run_async_evaluation`) ; "
        "interroger `GET .../evaluations/{evaluation_id}` pour suivre sa "
        "résolution (`completed`/`failed`). Borné par le plafond asynchrone "
        "déployé sur cette instance (`Settings.evaluation_async_max_sample_size`).\n\n"
        "`422` si l'évaluation n'est pas activée sur cette base "
        "(`KnowledgeBase.evaluation_enabled`), si `llm_provider_id` est "
        "inconnu ou non configuré, ou si `sample_size` dépasse le plafond "
        "asynchrone déployé. `409` si aucun document indexé n'est "
        "disponible dans cette base."
    ),
    responses={
        int(HTTPStatus.ACCEPTED): {
            "description": (
                "`sample_size` au-delà du plafond synchrone : run accepté, "
                'exécution asynchrone en cours (`status: "pending"`).'
            )
        },
        int(HTTPStatus.CONFLICT): {
            "description": "Aucun document indexé n'est disponible dans cette base."
        },
    },
    dependencies=[ScopeWrite],
)
async def create_evaluation(
    access: KbWrite,
    payload: EvaluationCreate,
    db: DbSession,
    settings: SettingsDep,
    response: Response,
) -> EvaluationRunRead:
    """Choisit le chemin synchrone/asynchrone, délègue au service — aucune autre logique ici."""
    service = EvaluationService(db, settings)
    if payload.sample_size > MAX_EVALUATION_SAMPLE_SIZE:
        result = await service.create_pending_run(access.principal, access.knowledge_base, payload)
        response.status_code = HTTPStatus.ACCEPTED
        return result
    return await service.create_run(access.principal, access.knowledge_base, payload)


@router.get(
    "/knowledge-bases/{kb_id}/evaluations",
    summary="Lister les évaluations d'une base",
    description=(
        "Historique résumé, du plus récent au plus ancien, sans les items "
        "(voir `GET .../evaluations/{evaluation_id}` pour le détail). Les "
        "scores agrégés (`average_judge_score`, `retrieval_recall_rate`) sont "
        "recalculés à la lecture, jamais stockés."
    ),
    dependencies=[ScopeRead],
)
async def list_evaluations(
    access: KbRead, db: DbSession, settings: SettingsDep
) -> list[EvaluationRunSummary]:
    """Délègue à `EvaluationService.list_runs`."""
    return await EvaluationService(db, settings).list_runs(access.knowledge_base)


@router.get(
    "/knowledge-bases/{kb_id}/evaluations/{evaluation_id}",
    summary="Lire le détail d'une évaluation",
    description="Run complet, items inclus — `404` s'il n'appartient pas à cette base.",
    dependencies=[ScopeRead],
)
async def read_evaluation(
    access: KbRead,
    evaluation_id: EvaluationPath,
    db: DbSession,
    settings: SettingsDep,
) -> EvaluationRunRead:
    """Délègue à `EvaluationService.get_run`."""
    return await EvaluationService(db, settings).get_run(access.knowledge_base, evaluation_id)


@router.get(
    "/evaluations/summary",
    summary="Vue globale des évaluations, toutes bases visibles confondues",
    description=(
        "Agrège recall@k/MRR/score de juge sur toutes les bases que le "
        "principal courant peut lire — même périmètre RBAC que "
        "`GET /api/v1/knowledge-bases`. Une base sans aucun item d'évaluation "
        "n'apparaît pas dans `knowledge_bases` (rien à mesurer), mais "
        "`overall` reste toujours renvoyé, même vide (`item_count: 0`, "
        "métriques `null`) si aucune base visible n'a encore été évaluée. "
        "`overall` n'est **pas** une moyenne des entrées par base : il est "
        "recalculé sur l'union de leurs items, pour ne pas pondérer chaque "
        "base également quel que soit son volume d'évaluation."
    ),
    dependencies=[ScopeRead],
)
async def read_evaluations_summary(
    principal: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> EvaluationsSummaryRead:
    """Délègue à `app.services.evaluation_summary.compute_visible_evaluation_summary`."""
    return await compute_visible_evaluation_summary(db, principal, settings=settings)
