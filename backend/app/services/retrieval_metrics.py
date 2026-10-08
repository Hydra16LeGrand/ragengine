"""Métriques IR (recall@k, MRR) du harnais de mesure retrieval — Phase 12, Étape 0.

Ne collecte rien de nouveau : recalcule recall@k et MRR à partir des
`EvaluationItem.retrieval_rank` déjà produits par `EvaluationService`
(Phase 8, ADR-017) — le rang 1-based du document source dans le top-k de
`RetrievalService.search()`, `NULL` s'il en est absent. Bibliothèque `ranx`
pour le calcul. Où vit durablement ce harnais (Postgres seul, export
Langfuse…) est l'Étape 1 de Phase 12 — arrêt obligatoire, pas encore
tranché : ce module ne présume rien de cette décision, il lit Postgres tel
qu'il existe aujourd'hui (plan interne).

**Reconstruction du classement à partir du seul rang connu.** `ranx` attend un
`Run` — un classement complet par requête, document → score — mais
`EvaluationItem` ne stocke que la position du document source, pas les
identités des documents qui l'entourent dans le top-k. On reconstruit un
classement synthétique équivalent : pour un item de rang `r`, on insère
`r - 1` documents fictifs (`__unranked__…`) à des scores strictement
supérieurs à celui du document source, puis le document source lui-même en
dernière position de ce groupe — son rang dans le `Run` reconstruit est donc
exactement `r`. Un item sans rang (`None`, absent du top-k) ne verse qu'un
document fictif jamais pertinent pour sa requête (cf. `compute_retrieval_
metrics`) : ni recall@k ni MRR ne peuvent le compter comme trouvé, quel que
soit `k`. Ce n'est pas une approximation : recall@k et MRR ne
dépendent que de la position du document pertinent, jamais de l'identité des
documents qui l'entourent — la reconstruction est donc exacte, vérifiée
empiriquement dans `tests/services/test_retrieval_metrics.py` contre les
formules calculées à la main.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, Field
from ranx import Qrels, Run, evaluate
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvaluationItem, EvaluationRun
from app.db.session import session_scope

DEFAULT_RECALL_K_VALUES: Final[tuple[int, ...]] = (1, 5)
"""k=1 (le document source est-il en tête ?) et k=5 (dans la moitié du top-k
par défaut de `RetrievalService.search()`, `top_k=10` — cf.
`app/domain/schemas/retrieval.py`)."""

_DUMMY_DOC_PREFIX: Final = "__unranked__"


class RetrievalIRMetrics(BaseModel):
    """Baseline IR d'une base de connaissances, agrégée sur tous ses runs."""

    knowledge_base_id: uuid.UUID
    run_count: int = Field(description="Nombre de runs d'évaluation agrégés.")
    item_count: int = Field(description="Nombre total d'items agrégés, tous runs confondus.")
    recall_at_k: dict[int, float | None] = Field(
        description=(
            "Proportion d'items dont le document source apparaît dans les k premiers "
            "résultats. `None` si `item_count == 0` (rien à mesurer, pas un score de 0)."
        )
    )
    mrr: float | None = Field(
        description="Reciprocal rank moyen sur tous les items ; `None` si `item_count == 0`."
    )


def compute_retrieval_metrics(
    items: Sequence[EvaluationItem],
    k_values: Sequence[int] = DEFAULT_RECALL_K_VALUES,
) -> tuple[dict[int, float | None], float | None]:
    """Calcule `(recall_at_k, mrr)` via `ranx`, à partir des seuls rangs connus.

    `None` partout si `items` est vide — distingue explicitement « rien à
    mesurer » de « mesuré à 0 », même convention que
    `_retrieval_recall_rate` dans `app/services/evaluations.py`.
    """
    if not items:
        return dict.fromkeys(k_values), None

    qrels_dict: dict[str, dict[str, int]] = {}
    run_dict: dict[str, dict[str, float]] = {}
    for item in items:
        query_id = str(item.id)
        doc_id = str(item.source_document_id)
        qrels_dict[query_id] = {doc_id: 1}

        rank = item.retrieval_rank
        if rank is None:
            # Un `Run` sans une seule entrée nulle part part fait planter `ranx`
            # (`max()` sur un itérable vide côté `numba`, constaté empiriquement
            # avec un lot où *tous* les items sont absents du top-k) : un
            # document fictif, jamais dans `qrels`, comble la requête sans
            # changer le résultat — toujours 0.0 pour cette requête, quel que
            # soit `k`.
            run_dict[query_id] = {f"{_DUMMY_DOC_PREFIX}{query_id}__absent": 0.0}
            continue

        entries: dict[str, float] = {}
        # Positions 1..r-1 : documents fictifs à score strictement décroissant,
        # tous au-dessus du document source (cf. docstring du module).
        for position in range(1, rank):
            entries[f"{_DUMMY_DOC_PREFIX}{query_id}__{position}"] = float(rank - position + 1)
        entries[doc_id] = 1.0
        run_dict[query_id] = entries

    qrels = Qrels(qrels_dict)
    run = Run(run_dict)
    metric_names = [f"recall@{k}" for k in k_values] + ["mrr"]
    scores = evaluate(qrels, run, metric_names)

    recall_at_k: dict[int, float | None] = {k: float(scores[f"recall@{k}"]) for k in k_values}
    mrr: float | None = float(scores["mrr"])
    return recall_at_k, mrr


async def compute_knowledge_base_retrieval_metrics(
    db: AsyncSession,
    knowledge_base_id: uuid.UUID,
    *,
    k_values: Sequence[int] = DEFAULT_RECALL_K_VALUES,
) -> RetrievalIRMetrics:
    """Baseline agrégée d'une base : tous les runs, tous les items, une seule vue.

    Contrairement à `EvaluationService`, qui résume un run à la fois, cette
    fonction additionne tous les runs d'une base — recall@k/MRR n'ont de sens
    qu'avec un volume d'items suffisant, rarement atteint par un seul run
    (`MAX_EVALUATION_SAMPLE_SIZE = 20`, cf. `app/domain/schemas/evaluation.py`).
    """
    items_statement = (
        select(EvaluationItem)
        .join(EvaluationRun, EvaluationItem.evaluation_run_id == EvaluationRun.id)
        .where(EvaluationRun.knowledge_base_id == knowledge_base_id)
    )
    items = (await db.execute(items_statement)).scalars().all()

    run_count_statement = (
        select(func.count())
        .select_from(EvaluationRun)
        .where(EvaluationRun.knowledge_base_id == knowledge_base_id)
    )
    run_count = (await db.execute(run_count_statement)).scalar_one()

    recall_at_k, mrr = compute_retrieval_metrics(items, k_values)
    return RetrievalIRMetrics(
        knowledge_base_id=knowledge_base_id,
        run_count=run_count,
        item_count=len(items),
        recall_at_k=recall_at_k,
        mrr=mrr,
    )


# ─── CLI — rejouer le calcul sans passer par l'API ─────────────────────────


async def _main(kb_ids: Sequence[uuid.UUID]) -> None:
    async with session_scope() as db:
        if kb_ids:
            targets = list(kb_ids)
        else:
            rows = (await db.execute(select(EvaluationRun.knowledge_base_id).distinct())).all()
            targets = [row[0] for row in rows]
        if not targets:
            print("Aucune base avec des runs d'évaluation.")
            return
        for kb_id in targets:
            metrics = await compute_knowledge_base_retrieval_metrics(db, kb_id)
            recall_str = ", ".join(
                f"recall@{k}={v:.3f}" if v is not None else f"recall@{k}=—"
                for k, v in metrics.recall_at_k.items()
            )
            mrr_str = f"{metrics.mrr:.3f}" if metrics.mrr is not None else "—"
            print(
                f"{kb_id}  runs={metrics.run_count:<3} items={metrics.item_count:<4} "
                f"{recall_str}  mrr={mrr_str}"
            )


def main() -> None:
    """`python -m app.services.retrieval_metrics [--kb-id UUID ...]`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kb-id",
        dest="kb_ids",
        action="append",
        type=uuid.UUID,
        default=[],
        help="Base(s) à mesurer ; répétable. Par défaut, toutes les bases avec au moins un run.",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.kb_ids))


if __name__ == "__main__":
    main()
