"""Port `Embedding` : interface structurelle et descripteur de capacités.

Les services n'importent que ce module — jamais un adaptateur concret
(`docs/providers-architecture.md` §0). Le port est un `Protocol` : un adaptateur
n'hérite de rien, il se contente d'exposer la bonne forme, ce qui garde les
implémentations (dont les fakes de test) indépendantes de toute base commune.

`embed_documents` et `embed_query` sont **distincts dès l'origine** : plusieurs
familles de modèles attendent un traitement asymétrique requête/passage (préfixe
d'instruction différent). Un adaptateur symétrique implémente les deux en
appelant la même méthode interne — c'est gratuit ; ajouter l'asymétrie après coup
ne l'aurait pas été, car cela change la signature du port et tous ses appelants
(`docs/providers-architecture.md` §1).
"""

from __future__ import annotations

from collections.abc import Sequence
from http import HTTPStatus
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import RAGEngineError


class EmbeddingProviderError(RAGEngineError):
    """Échec d'un fournisseur d'embeddings, traduit depuis son SDK ou son API HTTP.

    Aucun adaptateur ne laisse remonter une exception `httpx` ou `openai` brute
    (`docs/providers-architecture.md` §0) : le service appelant ne connaît que
    cette erreur. Le statut est `502 Bad Gateway` car la faute est celle d'un
    tiers en amont, pas celle de la requête entrante ; le fournisseur en cause
    est reporté dans l'extension `provider_id` du document Problem Details.
    """

    status: ClassVar[int] = HTTPStatus.BAD_GATEWAY
    title: ClassVar[str] = "Bad Gateway"
    type: ClassVar[str] = "/problems/embedding-provider-error"


class EmbeddingCapabilities(BaseModel):
    """Ce qu'une implémentation sait faire — jamais comment elle le fait.

    Immuable : un service peut l'inspecter (dimensions attendues, langues
    couvertes) et la conserver sans risquer de la voir muter sous ses pieds.
    """

    model_config = ConfigDict(frozen=True)

    provider_id: str = Field(description="Clé de résolution dans le registre, ex. « bge-m3 ».")
    dimensions: int = Field(gt=0, description="Taille des vecteurs produits.")
    max_batch_size: int = Field(gt=0, description="Nombre de textes par appel au fournisseur.")
    max_input_tokens: int = Field(gt=0, description="Longueur maximale d'un texte, en tokens.")
    languages: tuple[str, ...] | None = Field(
        default=None, description="Langues couvertes ; `None` = multilingue non restreint."
    )
    is_local: bool = Field(
        description="`True` = servi par le service d'inférence GPU dédié (ADR-002)."
    )


@runtime_checkable
class EmbeddingPort(Protocol):
    """Contrat de vectorisation attendu par les services d'ingestion et de retrieval.

    `runtime_checkable` sert aux assertions de test (`isinstance`) ; la
    vérification qui fait foi reste statique, via mypy, à l'endroit où un
    adaptateur est enregistré dans `registry.py`.
    """

    capabilities: EmbeddingCapabilities

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectorise des passages à indexer, dans l'ordre reçu."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Vectorise une requête utilisateur."""
        ...
