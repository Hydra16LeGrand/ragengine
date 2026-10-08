"""Catalogue des briques substituables — `GET /api/v1/providers`.

Agrège les quatre registres de `app/providers/` (`docs/providers-architecture.md`
§7). Route en lecture seule, sans authentification : c'est un catalogue de
capacités, pas une donnée utilisateur — un visiteur non connecté doit pouvoir
composer une configuration de base de connaissances avant de créer un compte
(ADR-005). Aucun appel réseau : chaque registre ne fait que refléter la
configuration (`Settings`), jamais une vérification de joignabilité (§0).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.dependencies import SettingsDep
from app.providers.embeddings.registry import EmbeddingProviderStatus, list_embedding_providers
from app.providers.engine import EngineProviderStatus, ingestion_engine_status
from app.providers.llm.registry import LLMProviderStatus, list_llm_providers
from app.providers.observability.factory import (
    ObservabilityProviderStatus,
    observability_provider_status,
)
from app.providers.rerankers.registry import RerankerProviderStatus, list_reranker_providers

router = APIRouter(tags=["providers"])


class ProviderCatalog(BaseModel):
    """Corps de réponse de `GET /api/v1/providers`."""

    model_config = ConfigDict(frozen=True)

    embeddings: list[EmbeddingProviderStatus]
    rerankers: list[RerankerProviderStatus]
    llm: list[LLMProviderStatus]
    engine: EngineProviderStatus = Field(
        description=(
            "Moteur d'ingestion du déploiement. `is_fake: true` : double en mémoire sans "
            "recherche sémantique, jamais servi en staging/production."
        )
    )
    observability: ObservabilityProviderStatus
    llm_forced_model_id: str | None = Field(
        default=None,
        description=(
            "Modèle imposé pour tout appel LLM que l'appelant ne paie pas de sa propre clé "
            "(`RAGENGINE_PLATFORM_LLM_MODEL_ID`). `null` : aucun, le modèle demandé est respecté."
        ),
    )


@router.get("/providers", response_model=ProviderCatalog)
async def get_providers(settings: SettingsDep) -> ProviderCatalog:
    """Renvoie le catalogue des briques disponibles et leur disponibilité."""
    return ProviderCatalog(
        embeddings=list_embedding_providers(settings),
        rerankers=list_reranker_providers(settings),
        llm=list_llm_providers(settings),
        engine=ingestion_engine_status(settings),
        observability=observability_provider_status(settings),
        llm_forced_model_id=settings.platform_llm_model_id,
    )
