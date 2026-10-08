"""Port `Reranker` : interface structurelle, résultat de reclassement, capacités.

Le port renvoie des **positions et des scores**, jamais les documents eux-mêmes :
le service appelant détient déjà les passages et leurs métadonnées, les faire
transiter en retour n'apporterait rien et brouillerait la responsabilité du port
(`docs/providers-architecture.md` §2).

Comme pour l'embedding, l'interface est un `Protocol` : le reranking désactivé
n'est pas une branche conditionnelle dans les services mais une implémentation à
part entière — `PassthroughReranker` (Null Object, CONTRIBUTING.md §3.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from http import HTTPStatus
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import RAGEngineError


class RerankerProviderError(RAGEngineError):
    """Échec d'un fournisseur de reranking, traduit depuis son API HTTP.

    Même contrat que pour l'embedding : aucune exception `httpx` ne quitte un
    adaptateur (§0), et le statut `502` désigne la défaillance d'un tiers en
    amont plutôt qu'une faute de la requête entrante.
    """

    status: ClassVar[int] = HTTPStatus.BAD_GATEWAY
    title: ClassVar[str] = "Bad Gateway"
    type: ClassVar[str] = "/problems/reranker-provider-error"


class RerankerCapabilities(BaseModel):
    """Ce qu'une implémentation de reranking sait faire, sans dire comment."""

    model_config = ConfigDict(frozen=True)

    provider_id: str = Field(description="Clé de résolution dans le registre.")
    max_documents: int = Field(gt=0, description="Taille maximale d'un lot de candidats.")
    languages: tuple[str, ...] | None = Field(
        default=None, description="Langues couvertes ; `None` = multilingue non restreint."
    )
    is_local: bool = Field(
        description="`True` = servi par le service d'inférence GPU dédié (ADR-002)."
    )


class RerankResult(BaseModel):
    """Score attribué à un candidat, désigné par sa position d'entrée."""

    index: int = Field(ge=0, description="Position dans la liste `documents` fournie.")
    score: float = Field(description="Pertinence attribuée ; l'échelle dépend du fournisseur.")


@runtime_checkable
class RerankerPort(Protocol):
    """Contrat de reclassement des candidats d'une recherche.

    Le résultat est trié par score décroissant et tronqué à `top_n` lorsqu'il est
    fourni. `runtime_checkable` sert aux assertions de test ; la vérification qui
    fait foi reste statique, à l'enregistrement dans `registry.py`.
    """

    capabilities: RerankerCapabilities

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        """Reclasse `documents` par pertinence vis-à-vis de `query`."""
        ...
