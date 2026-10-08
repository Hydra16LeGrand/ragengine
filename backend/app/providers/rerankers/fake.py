"""Reranker déterministe, pour les tests d'orchestration.

Le score dépend uniquement de la **position** d'entrée, et l'ordre produit est
l'**inverse** de l'ordre reçu. C'est délibéré : un fake qui renverrait les
candidats dans leur ordre d'arrivée serait indiscernable de `PassthroughReranker`
et laisserait passer un service qui ignore le résultat du reranking. En
inversant, tout test constate immédiatement si le classement du port a bien été
appliqué en aval.

Aucun réseau, aucun modèle, aucune dépendance à la requête.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.providers.rerankers.base import RerankerCapabilities, RerankResult

DEFAULT_FAKE_PROVIDER_ID: Final = "fake-reranker"


class FakeRerankerAdapter:
    """Implémentation déterministe du port `RerankerPort`."""

    def __init__(self, *, provider_id: str = DEFAULT_FAKE_PROVIDER_ID) -> None:
        self.capabilities: RerankerCapabilities = RerankerCapabilities(
            provider_id=provider_id,
            max_documents=1_000,
            languages=None,
            is_local=False,
        )

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        """Score croissant avec la position, donc classement inversé, tronqué à `top_n`."""
        total = len(documents)
        if total == 0:
            return []
        results = [
            RerankResult(index=index, score=(index + 1) / total) for index in reversed(range(total))
        ]
        return results if top_n is None else results[:top_n]
