"""Façade applicative RAGEngine (FastAPI).

Ce package expose l'API REST versionnée du moteur RAG : il orchestre les cas
d'usage métier et délègue toute technologie substituable aux adaptateurs de
`app.providers` (architecture hexagonale, cf. CONTRIBUTING.md §3).
"""

from typing import Final

__version__ = "0.1.0"
"""Version de la façade, source de vérité pour le build et l'endpoint de santé."""

API_VERSION: Final = "v1"
"""Version majeure de l'API REST — toute rupture de contrat crée une nouvelle version."""
