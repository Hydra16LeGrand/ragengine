"""Métriques IR (`app/services/retrieval_metrics.py`) — Phase 12, Étape 0.

`compute_retrieval_metrics` est une fonction pure (pas de DB) : les cas
principaux la ciblent directement avec des `EvaluationItem` non persistés,
vérifiés contre des formules calculées à la main. `compute_knowledge_base_
retrieval_metrics` (agrégation multi-runs depuis Postgres) n'est exercée
qu'une fois, contre une vraie base de test, pour couvrir la requête SQL —
la logique de calcul elle-même est déjà couverte sans DB.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvaluationItem
from app.services.retrieval_metrics import (
    compute_knowledge_base_retrieval_metrics,
    compute_retrieval_metrics,
)
from tests.db import factories
from tests.services.test_knowledge_bases import account


def _item(*, retrieval_rank: int | None) -> EvaluationItem:
    """Item non persisté, seul `retrieval_rank` varie — le reste est indifférent au calcul."""
    return EvaluationItem(
        id=uuid.uuid4(),
        evaluation_run_id=uuid.uuid4(),
        source_document_id=uuid.uuid4(),
        question="Q",
        reference_answer="R",
        generated_answer="R",
        retrieval_rank=retrieval_rank,
        judge_score=1.0,
        judge_rationale="ok",
    )


def test_no_items_returns_none_everywhere_not_zero() -> None:
    """`item_count == 0` : `None` explicite, jamais un score de 0 trompeur."""
    recall_at_k, mrr = compute_retrieval_metrics([])
    assert recall_at_k == {1: None, 5: None}
    assert mrr is None


def test_rank_one_everywhere_gives_perfect_scores() -> None:
    """Le document source est toujours en tête : recall@1 = recall@5 = MRR = 1.0."""
    items = [_item(retrieval_rank=1) for _ in range(4)]
    recall_at_k, mrr = compute_retrieval_metrics(items, k_values=(1, 5))
    assert recall_at_k == {1: 1.0, 5: 1.0}
    assert mrr == 1.0


def test_never_found_gives_zero_everywhere() -> None:
    """`retrieval_rank is None` partout (absent du top-k) : tout tombe à 0.0."""
    items = [_item(retrieval_rank=None) for _ in range(3)]
    recall_at_k, mrr = compute_retrieval_metrics(items, k_values=(1, 5))
    assert recall_at_k == {1: 0.0, 5: 0.0}
    assert mrr == 0.0


def test_mixed_ranks_match_hand_computed_formulas() -> None:
    """Trois items, rangs 1 / 3 / `None` — recall@k et MRR vérifiés à la main.

    recall@1 = 1/3 (seul le rang 1 est dans le top-1) ; recall@5 = 2/3 (rangs
    1 et 3 sont dans le top-5, pas l'absent) ; MRR = (1/1 + 1/3 + 0) / 3.
    """
    items = [
        _item(retrieval_rank=1),
        _item(retrieval_rank=3),
        _item(retrieval_rank=None),
    ]
    recall_at_k, mrr = compute_retrieval_metrics(items, k_values=(1, 5))
    assert recall_at_k[1] == 1 / 3
    assert recall_at_k[5] == 2 / 3
    assert mrr is not None
    assert abs(mrr - (1 + 1 / 3 + 0) / 3) < 1e-9


def test_rank_beyond_k_excluded_from_recall_but_counted_in_mrr() -> None:
    """Rang 5 avec k=(1,) seulement : recall@1 = 0, mais MRR reste 1/5, pas 0."""
    items = [_item(retrieval_rank=5)]
    recall_at_k, mrr = compute_retrieval_metrics(items, k_values=(1,))
    assert recall_at_k == {1: 0.0}
    assert mrr is not None
    assert abs(mrr - 1 / 5) < 1e-9


async def test_knowledge_base_aggregation_sums_items_across_runs(
    db_session: AsyncSession,
) -> None:
    """Deux runs sur la même base : `compute_knowledge_base_retrieval_metrics` les additionne."""
    owner = await account(db_session)
    knowledge_base = factories.make_knowledge_base(owner)
    db_session.add(knowledge_base)
    await db_session.flush()

    document_a = factories.make_document(knowledge_base, filename="a.pdf")
    document_b = factories.make_document(knowledge_base, filename="b.pdf")
    db_session.add_all([document_a, document_b])
    await db_session.flush()

    run_1 = factories.make_evaluation_run(knowledge_base, requested_by=owner)
    run_2 = factories.make_evaluation_run(knowledge_base, requested_by=owner)
    db_session.add_all([run_1, run_2])
    await db_session.flush()

    db_session.add_all(
        [
            factories.make_evaluation_item(run_1, document_a, retrieval_rank=1),
            factories.make_evaluation_item(run_2, document_b, retrieval_rank=None),
        ]
    )
    await db_session.flush()

    metrics = await compute_knowledge_base_retrieval_metrics(
        db_session, knowledge_base.id, k_values=(1, 5)
    )

    assert metrics.run_count == 2
    assert metrics.item_count == 2
    assert metrics.recall_at_k == {1: 0.5, 5: 0.5}
    assert metrics.mrr == 0.5


async def test_knowledge_base_with_no_runs_returns_none_metrics(
    db_session: AsyncSession,
) -> None:
    """Base sans aucun run : `run_count == 0`, métriques `None` plutôt que 0.0."""
    owner = await account(db_session)
    knowledge_base = factories.make_knowledge_base(owner, slug="sans-evaluation")
    db_session.add(knowledge_base)
    await db_session.flush()

    metrics = await compute_knowledge_base_retrieval_metrics(db_session, knowledge_base.id)

    assert metrics.run_count == 0
    assert metrics.item_count == 0
    assert metrics.recall_at_k == {1: None, 5: None}
    assert metrics.mrr is None
