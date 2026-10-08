"""Reranking désactivé — implémentation Null Object du port `RerankerPort`.

C'est l'implémentation résolue quand une base de connaissances a
`reranker_model_id IS NULL`. Le service de retrieval appelle donc *toujours* un
reranker : il n'y a pas de `if kb.reranker_model_id is not None` dispersé dans
l'orchestration (CONTRIBUTING.md §3.4, `docs/providers-architecture.md` §2).

L'ordre d'entrée est préservé tel quel — c'est déjà celui du moteur de recherche
hybride — et le score est **constant** : un score constant dit explicitement
« aucune information de pertinence n'a été ajoutée », là où des scores dégressifs
inventés laisseraient croire à un classement.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.core.config import Settings
from app.providers.rerankers.base import RerankerCapabilities, RerankResult

PASSTHROUGH_SCORE: Final = 1.0
"""Score attribué à tout candidat : constant, donc non informatif par construction."""

PASSTHROUGH_CAPABILITIES: Final = RerankerCapabilities(
    provider_id="passthrough",
    # Aucun modèle, aucun coût : la seule borne raisonnable est celle du lot que
    # le moteur de recherche peut produire.
    max_documents=10_000,
    languages=None,
    # `False` : rien ne tourne sur le GPU, donc rien à réserver côté VRAM.
    is_local=False,
)
"""Capacités triviales du Null Object, enregistrées au catalogue comme les autres."""


class PassthroughReranker:
    """Rend les candidats dans leur ordre d'arrivée, avec un score constant."""

    def __init__(self, capabilities: RerankerCapabilities = PASSTHROUGH_CAPABILITIES) -> None:
        self.capabilities: RerankerCapabilities = capabilities

    @classmethod
    def from_settings(cls, settings: Settings) -> PassthroughReranker:
        """Signature de fabrique commune au registre ; aucune configuration n'est lue."""
        return cls()

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        """Renvoie les positions 0..n-1 dans l'ordre, tronquées à `top_n`."""
        results = [
            RerankResult(index=index, score=PASSTHROUGH_SCORE) for index in range(len(documents))
        ]
        return results if top_n is None else results[:top_n]
