"""Adaptateur de reranking local : le service d'inférence GPU dédié (ADR-002).

Depuis ADR-051, le service est **Text Embeddings Inference** (TEI, HuggingFace),
un conteneur mono-modèle dédié à bge-reranker-v2-m3 (`docker/docker-compose.gpu.
yml`, service `inference-reranker`) — remplace Xinference (ADR-011), qui
sérialisait strictement les requêtes concurrentes sans aucun batching. Construire
l'adaptateur n'ouvre aucune socket ; une panne du service ressort en
`RerankerProviderError`.

Contrat HTTP : `POST {inference_base_url}/rerank`, le dialecte **natif** de TEI
(pas la route `/v1/rerank`, qui n'existe pas côté TEI — RAGFlow lui-même
(`rag/llm/rerank_model.py::HuggingfaceRerank.post`) poste sur `/rerank`).

    → {"query": "…", "texts": ["…"], "raw_scores": false}
    ← [{"index": 2, "score": 0.91}, …]

**Contrat confirmé en lisant la source réelle de TEI** (`router/src/http/
types.rs::RerankRequest`/`Rank`, `router/src/http/server.rs::rerank`), pas
seulement d'après la documentation : requête `{query, texts, truncate?,
truncation_direction?, raw_scores, return_text}` — noter `texts`, pas
`documents`, et aucun champ `model` (un conteneur TEI ne sert qu'un seul modèle,
rien à désambiguïser) — et réponse en **tableau nu**, déjà trié par score
décroissant côté serveur (`ranks.sort_by(...); ranks.reverse()`), que
l'adaptateur retrie de toute façon sans coût pour ne pas dépendre de ce détail
d'implémentation.

`return_text` est omis (défaut `false` côté TEI) : le port ne renvoie que des
positions et des scores, rapatrier les textes doublerait la charge utile pour
rien. TEI ne borne le lot que par `max_client_batch_size` (configuration serveur,
pas un paramètre de requête) : c'est `RerankerCapabilities.max_documents`
ci-dessous qui reste l'unique garde côté RAGEngine.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import httpx
from pydantic import BaseModel, RootModel
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.rerankers.base import (
    RerankerCapabilities,
    RerankerProviderError,
    RerankResult,
)

BGE_RERANKER_V2_M3_CAPABILITIES: Final = RerankerCapabilities(
    provider_id="bge-reranker-v2-m3",
    # Plafond conservateur : un reranking porte sur les candidats d'une recherche
    # (quelques dizaines), et chaque paire (requête, passage) est un forward
    # complet sur un GPU au budget contraint (CONTRIBUTING.md §2).
    max_documents=64,
    # Multilingue non restreint, comme BGE-M3 (ADR-003).
    languages=None,
    is_local=True,
)
"""Capacités du reranker local par défaut (`settings.reranker_model_id`)."""

RERANK_PATH: Final = "/rerank"
DEFAULT_TIMEOUT_SECONDS: Final = 60.0


class _RerankItem(BaseModel):
    """Un candidat reclassé : sa position d'entrée et son score."""

    index: int
    score: float


class _RerankResponse(RootModel[list[_RerankItem]]):
    """Réponse du service d'inférence : un tableau nu, pas d'enveloppe (contrat TEI)."""


class LocalRerankerAdapter:
    """Client HTTP du service d'inférence GPU, conforme au port `RerankerPort`."""

    def __init__(
        self,
        *,
        base_url: str,
        capabilities: RerankerCapabilities = BGE_RERANKER_V2_M3_CAPABILITIES,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.capabilities: RerankerCapabilities = capabilities
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Client créé au premier reclassement : résoudre un provider ne doit
        # allouer aucune ressource réseau (`docs/providers-architecture.md` §8).
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, settings: Settings) -> LocalRerankerAdapter:
        """Construit l'adaptateur depuis la configuration (aucune URL en dur)."""
        return cls(base_url=settings.reranker_inference_base_url)

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        """Reclasse les candidats via le service d'inférence.

        Un lot plus grand que `capabilities.max_documents` est **refusé**, pas
        tronqué en silence : un appelant qui dépasse la capacité annoncée a un
        bug de sélection de candidats, et une troncature muette le masquerait.

        TEI n'a pas de paramètre `top_n` : il reclasse le lot entier et le
        renvoie trié. La troncature au `top_n` demandé reste donc entièrement
        côté adaptateur (`_to_results`), comme c'était déjà le cas.
        """
        if not documents:
            return []
        if len(documents) > self.capabilities.max_documents:
            raise ValidationError(
                f"Le reranker « {self.capabilities.provider_id} » accepte au plus "
                f"{self.capabilities.max_documents} documents ; {len(documents)} reçus."
            )
        payload: dict[str, object] = {
            "query": query,
            "texts": list(documents),
            "raw_scores": False,
        }
        parsed = await self._post(payload)
        return self._to_results(parsed, candidates=len(documents), top_n=top_n)

    async def aclose(self) -> None:
        """Ferme le client HTTP si l'adaptateur en est propriétaire."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _http_client(self) -> httpx.AsyncClient:
        """Renvoie le client, créé paresseusement au premier appel réseau."""
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    async def _post(self, payload: dict[str, object]) -> _RerankResponse:
        """Exécute l'appel, et traduit toute défaillance du tiers."""
        try:
            response = await self._http_client().post(RERANK_PATH, json=payload)
            response.raise_for_status()
            return _RerankResponse.model_validate(response.json())
        except httpx.HTTPStatusError as error:
            raise self._error(
                f"Le service d'inférence a répondu {error.response.status_code}.",
                status_code=error.response.status_code,
            ) from error
        except httpx.HTTPError as error:
            raise self._error(f"Service d'inférence injoignable : {error}.") from error
        except (PydanticValidationError, ValueError) as error:
            raise self._error("Réponse illisible du service d'inférence.") from error

    def _to_results(
        self, parsed: _RerankResponse, *, candidates: int, top_n: int | None
    ) -> list[RerankResult]:
        """Vérifie les positions renvoyées, trie par score décroissant, tronque."""
        items = parsed.root
        if any(not 0 <= item.index < candidates for item in items):
            raise self._error("Le service d'inférence a renvoyé une position hors du lot envoyé.")
        results = [
            RerankResult(index=item.index, score=item.score)
            for item in sorted(items, key=lambda item: item.score, reverse=True)
        ]
        return results if top_n is None else results[:top_n]

    def _error(self, detail: str, *, status_code: int | None = None) -> RerankerProviderError:
        """Assemble l'erreur de port, en nommant le fournisseur fautif."""
        return RerankerProviderError(
            detail,
            extensions={"provider_id": self.capabilities.provider_id, "status_code": status_code},
        )
