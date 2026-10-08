"""Agrégation transverse d'évaluation — `GET /api/v1/evaluations/summary` (Phase 12, Étape 5).

`EvaluationsPanel.tsx` (frontend) et `EvaluationService` (Phase 8) répondent
tous les deux « comment se comporte *cette* base » — jamais « comment se
comporte le déploiement dans l'ensemble ». Ce module ferme cet écart en
réutilisant `compute_retrieval_metrics` (`app.services.retrieval_metrics`,
Phase 12 Étape 0, ranx) pour le calcul pur, et en l'entourant du seul
morceau qui manquait : une portée **RBAC-consciente**, jamais un `--kb-id`
de script CLI.

**RBAC** : le périmètre parcouru est exactement celui de `GET
/api/v1/knowledge-bases` (`KnowledgeBaseService.list_visible`) — jamais un
second calcul d'autorisation. Une base visible mais sans aucun item
d'évaluation compte dans ce périmètre (elle est parcourue) mais n'apparaît
pas dans `EvaluationsSummaryRead.knowledge_bases` (rien à mesurer) ; c'est
uniquement une question d'affichage, pas de fuite RBAC — aucune donnée d'une
base invisible n'entre jamais dans la requête qui charge les items.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import EvaluationItem, EvaluationRun, KnowledgeBase
from app.domain.pagination import MAX_PAGE_SIZE, build_page
from app.domain.schemas.evaluation_summary import (
    EvaluationsOverallSummary,
    EvaluationsSummaryRead,
    KnowledgeBaseEvaluationSummary,
)
from app.services.identity.principal import Principal
from app.services.knowledge_bases import KnowledgeBaseService
from app.services.retrieval_metrics import DEFAULT_RECALL_K_VALUES, compute_retrieval_metrics


def _average_judge_score(items: Sequence[EvaluationItem]) -> float | None:
    """Même calcul que `app.services.evaluations._average_judge_score` — dupliqué à dessein :
    portée différente (transverse, pas un run), pas de raison de coupler les deux modules
    pour cette seule fonction pure d'une ligne."""
    if not items:
        return None
    return sum(item.judge_score for item in items) / len(items)


async def _all_visible_knowledge_base_ids(
    kb_service: KnowledgeBaseService, principal: Principal
) -> list[uuid.UUID]:
    """Parcourt toutes les pages de `list_visible` — le périmètre RBAC exact de la liste des bases.

    `list_visible` est déjà paginée par curseur (`GET /api/v1/knowledge-bases`) ;
    ce tableau de bord agrège *tout* le périmètre visible, jamais une seule
    page. Boucle bornée par construction : elle s'arrête dès que `build_page`
    ne renvoie plus de `next_cursor`.
    """
    ids: list[uuid.UUID] = []
    cursor: str | None = None
    while True:
        rows = await kb_service.list_visible(principal, cursor=cursor, limit=MAX_PAGE_SIZE)
        page = build_page(rows, serializer=lambda kb: kb.id, limit=MAX_PAGE_SIZE)
        ids.extend(page.data)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    return ids


def _empty_overall(k_values: Sequence[int]) -> EvaluationsOverallSummary:
    return EvaluationsOverallSummary(
        knowledge_base_count=0,
        run_count=0,
        item_count=0,
        recall_at_k=dict.fromkeys(k_values),
        mrr=None,
        average_judge_score=None,
    )


async def compute_visible_evaluation_summary(
    db: AsyncSession,
    principal: Principal,
    *,
    settings: Settings | None = None,
    k_values: Sequence[int] = DEFAULT_RECALL_K_VALUES,
) -> EvaluationsSummaryRead:
    """Agrège recall@k/MRR/score de juge sur toutes les bases visibles par `principal`.

    Deux requêtes seulement, quel que soit le nombre de bases visibles : les
    items (jointure `EvaluationItem` → `EvaluationRun` → `KnowledgeBase`,
    filtrée sur le périmètre RBAC) puis le compte de runs par base — jamais
    une requête par base (pas de N+1), contrairement à un appel répété de
    `compute_knowledge_base_retrieval_metrics` (`app.services.retrieval_metrics`,
    conçu pour un `--kb-id` unique, pas pour ce cas d'usage transverse).
    """
    resolved_settings = settings if settings is not None else get_settings()
    kb_service = KnowledgeBaseService(db, resolved_settings)
    visible_ids = await _all_visible_knowledge_base_ids(kb_service, principal)
    if not visible_ids:
        return EvaluationsSummaryRead(knowledge_bases=[], overall=_empty_overall(k_values))

    items_statement = (
        select(EvaluationItem, EvaluationRun.knowledge_base_id, KnowledgeBase.name)
        .join(EvaluationRun, EvaluationItem.evaluation_run_id == EvaluationRun.id)
        .join(KnowledgeBase, EvaluationRun.knowledge_base_id == KnowledgeBase.id)
        .where(EvaluationRun.knowledge_base_id.in_(visible_ids))
    )
    rows = (await db.execute(items_statement)).all()

    items_by_kb: dict[uuid.UUID, list[EvaluationItem]] = {}
    names_by_kb: dict[uuid.UUID, str] = {}
    for item, knowledge_base_id, knowledge_base_name in rows:
        items_by_kb.setdefault(knowledge_base_id, []).append(item)
        names_by_kb[knowledge_base_id] = knowledge_base_name

    if not items_by_kb:
        return EvaluationsSummaryRead(knowledge_bases=[], overall=_empty_overall(k_values))

    run_count_statement = (
        select(EvaluationRun.knowledge_base_id, func.count())
        .where(EvaluationRun.knowledge_base_id.in_(visible_ids))
        .group_by(EvaluationRun.knowledge_base_id)
    )
    run_counts: dict[uuid.UUID, int] = dict((await db.execute(run_count_statement)).tuples().all())

    entries = [
        _to_kb_summary(
            knowledge_base_id=knowledge_base_id,
            knowledge_base_name=names_by_kb[knowledge_base_id],
            run_count=run_counts.get(knowledge_base_id, 0),
            items=items,
            k_values=k_values,
        )
        for knowledge_base_id, items in items_by_kb.items()
    ]
    entries.sort(key=lambda entry: entry.knowledge_base_name)

    all_items = [item for items in items_by_kb.values() for item in items]
    recall_at_k, mrr = compute_retrieval_metrics(all_items, k_values)
    overall = EvaluationsOverallSummary(
        knowledge_base_count=len(items_by_kb),
        run_count=sum(run_counts.get(kb_id, 0) for kb_id in items_by_kb),
        item_count=len(all_items),
        recall_at_k=recall_at_k,
        mrr=mrr,
        average_judge_score=_average_judge_score(all_items),
    )
    return EvaluationsSummaryRead(knowledge_bases=entries, overall=overall)


def _to_kb_summary(
    *,
    knowledge_base_id: uuid.UUID,
    knowledge_base_name: str,
    run_count: int,
    items: Sequence[EvaluationItem],
    k_values: Sequence[int],
) -> KnowledgeBaseEvaluationSummary:
    recall_at_k, mrr = compute_retrieval_metrics(items, k_values)
    return KnowledgeBaseEvaluationSummary(
        knowledge_base_id=knowledge_base_id,
        knowledge_base_name=knowledge_base_name,
        run_count=run_count,
        item_count=len(items),
        recall_at_k=recall_at_k,
        mrr=mrr,
        average_judge_score=_average_judge_score(items),
    )
