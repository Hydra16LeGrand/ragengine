"""Port `IngestionEngine` et son adaptateur RAGFlow.

RAGFlow (Slim) est le moteur unique d'ingestion, d'indexation et de retrieval
(ADR-001). Il est consommé exclusivement via son API HTTP encapsulée ici :
aucun autre module du dépôt ne connaît RAGFlow.

**Un registre, une implémentation de production** (`registry.py`, ADR-070) :
`resolve_ingestion_engine` est le point unique où le nom `RAGFlow` rencontre le
reste de l'application ; services et tâches Celery n'obtiennent d'elle qu'un
`IngestionEnginePort`. Le moteur se choisit par `INGESTION_ENGINE` ; `fake`
(`fake.py`) est un double en mémoire, refusé en staging/production.
"""

from __future__ import annotations

from app.providers.engine.base import IngestionEnginePort
from app.providers.engine.registry import (
    EngineProviderStatus,
    ingestion_engine_status,
    resolve_ingestion_engine,
)

__all__ = [
    "EngineProviderStatus",
    "IngestionEnginePort",
    "ingestion_engine_status",
    "resolve_ingestion_engine",
]
