"""Adaptateur RAGFlow d'`IngestionEnginePort` (ADR-001, ADR-012).

Seul module du dépôt autorisé à connaître les endpoints, les codes d'erreur et
les formes de réponse de RAGFlow (CONTRIBUTING.md §3.3). Tout le reste passe par
`app.providers.engine.base`.
"""

from app.providers.engine.ragflow.client import RagflowEngineAdapter

__all__ = ["RagflowEngineAdapter"]
