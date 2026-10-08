"""Schémas de l'agrégation transverse d'évaluation — `GET /api/v1/evaluations/summary`.

Phase 12, Étape 5 : « notre retrieval est-il bon dans l'ensemble », pas
seulement base par base (`EvaluationsPanel.tsx` du frontend, jusqu'ici
strictement par base). Portée RBAC identique à `GET /api/v1/knowledge-bases`
(`app.services.evaluation_summary`) : ces schémas ne portent que la forme de
la réponse, jamais de décision d'autorisation.
"""

from __future__ import annotations

import uuid
from typing import Final

from pydantic import Field

from app.domain.schemas.common import ReadModel

_RECALL_DESCRIPTION: Final = (
    "Proportion d'items dont le document source apparaît dans les k premiers "
    "résultats de `RetrievalService.search()` ; `null` pour un `k` sans aucun "
    "item mesuré. Mêmes valeurs de `k` que `app.services.retrieval_metrics."
    "DEFAULT_RECALL_K_VALUES`."
)


class KnowledgeBaseEvaluationSummary(ReadModel):
    """Métriques IR agrégées d'une base — une entrée de `EvaluationsSummaryRead.knowledge_bases`.

    N'apparaît dans la liste que si la base porte au moins un item
    d'évaluation (rien à mesurer sinon) — voir la docstring du service pour
    la distinction avec le périmètre RBAC, plus large.
    """

    knowledge_base_id: uuid.UUID
    knowledge_base_name: str
    run_count: int = Field(description="Nombre de runs d'évaluation de cette base, tous statuts.")
    item_count: int = Field(description="Nombre total d'items agrégés, tous runs confondus.")
    recall_at_k: dict[int, float | None] = Field(description=_RECALL_DESCRIPTION)
    mrr: float | None = Field(description="Reciprocal rank moyen sur les items de cette base.")
    average_judge_score: float | None = Field(
        description="Moyenne de `judge_score` sur les items de cette base."
    )


class EvaluationsOverallSummary(ReadModel):
    """Mêmes métriques, recalculées sur l'union des items de toutes les bases visibles.

    **Jamais** une moyenne des métriques par base : recall@k/MRR sont
    recalculés sur l'ensemble des items regroupés (`compute_retrieval_metrics`,
    `app.services.retrieval_metrics`) — une moyenne de proportions par base
    pondérerait chaque base également quel que soit son volume, ce qui
    fausserait la lecture « dans l'ensemble » pour un déploiement dont les
    bases ont des volumes d'évaluation très inégaux.
    """

    knowledge_base_count: int = Field(
        description="Bases visibles portant au moins un item d'évaluation."
    )
    run_count: int
    item_count: int
    recall_at_k: dict[int, float | None] = Field(description=_RECALL_DESCRIPTION)
    mrr: float | None
    average_judge_score: float | None


class EvaluationsSummaryRead(ReadModel):
    """Réponse de `GET /api/v1/evaluations/summary`."""

    knowledge_bases: list[KnowledgeBaseEvaluationSummary] = Field(
        description="Une entrée par base visible ayant au moins un item d'évaluation, "
        "triée par nom de base."
    )
    overall: EvaluationsOverallSummary


__all__ = [
    "EvaluationsOverallSummary",
    "EvaluationsSummaryRead",
    "KnowledgeBaseEvaluationSummary",
]
