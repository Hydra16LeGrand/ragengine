"""Schémas de la génération d'évaluation (Phase 8) — voir `docs/adr/017-*.md`.

Les scores agrégés (`average_judge_score`, `retrieval_recall_rate`) ne sont
**jamais** lus depuis une colonne : ils sont recalculés par le service à chaque
lecture depuis les `EvaluationItem` du run (`app/services/evaluations.py`), ce
qui interdit toute divergence entre un total stocké et le détail qui l'a
produit. Ces schémas sont donc construits champ par champ par le service,
jamais par `model_validate(orm_object)` — contrairement au reste du dépôt, un
`EvaluationRun` seul ne porte pas assez d'information pour se sérialiser.

**Deux chemins d'exécution** (Phase 12 Étape 4, ADR-028), un seul schéma de
requête (`EvaluationCreate`) : `sample_size ≤ MAX_EVALUATION_SAMPLE_SIZE`
(20) reste **synchrone** — comportement inchangé, `201` avec le run complet.
Au-delà, `POST .../evaluations` bascule en **asynchrone** — `202`, run
`pending` sans items, borné par `Settings.evaluation_async_max_sample_size`
(non importé ici, résolu par le service : coupler ce module à la
configuration runtime pour une seule borne aurait été disproportionné).
`ABSOLUTE_MAX_EVALUATION_SAMPLE_SIZE` n'est qu'un garde-fou Pydantic anti-abus
statique — la borne métier réelle du chemin asynchrone reste dynamique.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from pydantic import Field

from app.domain.enums import EvaluationRunStatus
from app.domain.schemas.common import MAX_PROVIDER_ID_LENGTH, InputModel, ReadModel

MIN_EVALUATION_SAMPLE_SIZE: Final = 1
DEFAULT_EVALUATION_SAMPLE_SIZE: Final = 5
MAX_EVALUATION_SAMPLE_SIZE: Final = 20
"""Plafond du chemin **synchrone** : chaque document échantillonné coûte au
moins deux appels LLM réels (génération de la Q/R, jugement) plus un
aller-retour retrieval — CONTRIBUTING.md §1, la génération est externalisée et
facturée par le fournisseur. Au-delà, `POST .../evaluations` bascule en
asynchrone (ADR-028) plutôt que de continuer à bloquer la requête HTTP."""

ABSOLUTE_MAX_EVALUATION_SAMPLE_SIZE: Final = 1000
"""Garde-fou Pydantic statique, jamais la vraie borne métier du chemin
asynchrone (`Settings.evaluation_async_max_sample_size`, dynamique — cf.
`EvaluationService.create_pending_run`) : seulement de quoi refuser une
requête absurde (`422`) avant toute requête base de données."""


class EvaluationCreate(InputModel):
    """Requête de lancement — `POST /api/v1/knowledge-bases/{kb_id}/evaluations`.

    Synchrone (`201`) si `sample_size ≤ MAX_EVALUATION_SAMPLE_SIZE` (20),
    asynchrone (`202`, run `pending`) sinon — voir la docstring du module.
    """

    sample_size: int = Field(
        default=DEFAULT_EVALUATION_SAMPLE_SIZE,
        ge=MIN_EVALUATION_SAMPLE_SIZE,
        le=ABSOLUTE_MAX_EVALUATION_SAMPLE_SIZE,
        description=(
            "Nombre de documents échantillonnés — une question générée par "
            f"document. Jusqu'à {MAX_EVALUATION_SAMPLE_SIZE} : run synchrone "
            "(réponse `201`, run complet). Au-delà : run asynchrone (`202`, "
            "borné par le plafond déployé — `422` si dépassé)."
        ),
    )
    llm_provider_id: str = Field(
        min_length=1,
        max_length=MAX_PROVIDER_ID_LENGTH,
        description=(
            "Identifiant du modèle utilisé à la fois pour générer les Q/R et pour "
            "juger (catalogue réel via `GET /api/v1/providers`, champ `llm`). "
            "Obligatoire : jamais de repli silencieux sur un modèle par défaut, "
            "même convention que `RetrievalChatRequest.llm_provider_id`."
        ),
    )


class EvaluationItemRead(ReadModel):
    """Une question générée, sa réponse de référence et sa notation."""

    id: uuid.UUID
    source_document_id: uuid.UUID = Field(
        description="Document dont le passage a produit la question."
    )
    question: str
    reference_answer: str = Field(
        description="Réponse de référence, produite avec le passage source."
    )
    generated_answer: str = Field(
        description="Réponse produite par `RetrievalService.chat()` pour cette question."
    )
    retrieval_rank: int | None = Field(
        description=(
            "Position (1-based) de `source_document_id` dans le top-k de "
            "`RetrievalService.search()` pour cette question ; `null` s'il est absent."
        )
    )
    judge_score: float = Field(ge=0.0, le=1.0, description="Note du juge LLM, 0.0 à 1.0.")
    judge_rationale: str
    created_at: datetime


class EvaluationRunSummary(ReadModel):
    """Résumé d'un run, sans ses items — `GET .../evaluations` (historique)."""

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    requested_by_id: uuid.UUID | None = Field(
        description="Compte à l'origine du lancement ; `null` si supprimé depuis, ou éphémère."
    )
    llm_provider_id: str
    sample_size: int
    status: EvaluationRunStatus = Field(
        description=(
            "`pending` : run asynchrone encore en cours (aucun item pour l'instant). "
            "`completed`/`failed` : résolu — un run synchrone est toujours déjà "
            "`completed` dès sa création (ADR-028)."
        )
    )
    item_count: int = Field(description="Nombre d'items effectivement produits (≤ `sample_size`).")
    average_judge_score: float | None = Field(
        description="Moyenne de `judge_score` sur les items du run ; `null` si `item_count == 0`."
    )
    retrieval_recall_rate: float | None = Field(
        description=(
            "Proportion d'items dont `retrieval_rank` n'est pas `null` ; `null` si "
            "`item_count == 0`."
        )
    )
    created_at: datetime


class EvaluationRunRead(EvaluationRunSummary):
    """Détail complet d'un run — `POST .../evaluations` et `GET .../evaluations/{id}`."""

    items: list[EvaluationItemRead] = Field(default_factory=list)
