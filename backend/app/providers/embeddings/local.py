"""Adaptateur d'embedding local : le service d'inférence GPU dédié (ADR-002).

Depuis ADR-051, le service est **Text Embeddings Inference** (TEI, HuggingFace),
un conteneur mono-modèle dédié à BGE-M3 (`docker/docker-compose.gpu.yml`, service
`inference-embedding`) — remplace Xinference (ADR-011), qui sérialisait
strictement les requêtes concurrentes derrière un verrou interne, sans aucun
batching, plafonnant le débit d'ingestion en usage concurrent. TEI fait du
batching dynamique natif pour ce type de modèle. Construire l'adaptateur reste
inoffensif — aucune socket n'est ouverte tant qu'aucun texte n'est vectorisé
(`docs/providers-architecture.md` §8) ; une panne du service ressort en
`EmbeddingProviderError`.

Aucun modèle n'est chargé dans le process FastAPI : le GPU appartient au service
d'inférence, seul détenteur du budget VRAM (CONTRIBUTING.md §2).

Contrat HTTP : `POST {inference_base_url}/v1/embeddings`, forme
OpenAI-compatible — TEI l'expose nativement (`router/src/http/server.rs::
openai_embed`), à côté de sa route native `/embed` (celle qu'utilise RAGFlow,
`rag/llm/embedding_model.py::HuggingFaceEmbed`, dans un dialecte différent).
Contrat inchangé par la migration Xinference → TEI.

    → {"model": "bge-m3", "input": ["texte 1", "texte 2"]}
    ← {"data": [{"index": 0, "embedding": [0.1, ...]}, {"index": 1, ...}]}

**Contrat reconfirmé contre une instance TEI réelle** (ADR-051), après l'avoir
déjà été contre Xinference en Phase 3 : même forme `{"data": [{"index",
"embedding"}, ...]}`, dimension 1024 conforme à `BGE_M3_CAPABILITIES`. Le champ
`model` envoyé ("bge-m3") ne correspond pas à l'identifiant HuggingFace réel
chargé par TEI ("BAAI/bge-m3") — sans conséquence : TEI ne sert qu'un seul modèle
par conteneur et ne fait que journaliser un avertissement en cas de
non-correspondance (`server.rs::openai_embed`, commentaire explicite : « Text
Embeddings Inference can only serve a single model at a time so no need for the
`model` parameter »), il ne rejette jamais la requête pour ce motif.

Les entrées sont découpées en lots de `settings.embedding_batch_size`
(défaut conservateur, cf. CONTRIBUTING.md §2). Le sémaphore VRAM applicatif reste ce
plafond côté RAGEngine ; TEI batch nativement côté serveur (contrairement à
Xinference, qui sérialisait), sans jamais dépasser le modèle unique chargé au
démarrage du conteneur.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import batched
from typing import Final

import httpx
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings
from app.providers.embeddings.base import (
    EmbeddingCapabilities,
    EmbeddingProviderError,
)

BGE_M3_CAPABILITIES: Final = EmbeddingCapabilities(
    provider_id="bge-m3",
    dimensions=1024,
    # Plafond du port ; le lot effectif vient de `settings.embedding_batch_size`.
    max_batch_size=64,
    max_input_tokens=8192,
    # Multilingue non restreint : c'est la raison même du choix de BGE-M3
    # pour un corpus francophone ouvert (CONTRIBUTING.md §1, ADR-003).
    languages=None,
    is_local=True,
)
"""Capacités du modèle d'embedding local par défaut (`settings.embedding_model_id`)."""

EMBEDDINGS_PATH: Final = "/v1/embeddings"
DEFAULT_TIMEOUT_SECONDS: Final = 60.0


class _EmbeddingItem(BaseModel):
    """Un vecteur de la réponse, avec sa position dans la requête."""

    index: int
    embedding: list[float]


class _EmbeddingResponse(BaseModel):
    """Réponse du service d'inférence, réduite aux champs dont dépend l'adaptateur."""

    data: list[_EmbeddingItem]


class LocalEmbeddingAdapter:
    """Client HTTP du service d'inférence GPU, conforme au port `EmbeddingPort`."""

    def __init__(
        self,
        *,
        base_url: str,
        capabilities: EmbeddingCapabilities = BGE_M3_CAPABILITIES,
        batch_size: int | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.capabilities: EmbeddingCapabilities = capabilities
        self._base_url = base_url.rstrip("/")
        self._batch_size = min(
            batch_size or capabilities.max_batch_size, capabilities.max_batch_size
        )
        self._timeout = timeout
        # Client créé à la première vectorisation : résoudre un provider (au
        # moment de valider une base de connaissances, par exemple) ne doit
        # allouer aucune ressource réseau — `docs/providers-architecture.md` §8.
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, settings: Settings) -> LocalEmbeddingAdapter:
        """Construit l'adaptateur depuis la configuration (aucun secret en dur)."""
        return cls(
            base_url=settings.embedding_inference_base_url,
            batch_size=settings.embedding_batch_size,
        )

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectorise des passages, lot par lot, en préservant l'ordre d'entrée."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for batch in batched(texts, self._batch_size):
            vectors.extend(await self._embed(list(batch)))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Vectorise une requête.

        BGE-M3 est **symétrique** : contrairement aux `bge-*-en-v1.5`, il ne
        demande aucun préfixe d'instruction côté requête. La méthode délègue donc
        au même appel interne — l'asymétrie reste possible sans toucher au port.
        """
        vectors = await self._embed([text])
        return vectors[0]

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

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Exécute un appel, et traduit toute défaillance du tiers."""
        payload = {"model": self.capabilities.provider_id, "input": texts}
        try:
            response = await self._http_client().post(EMBEDDINGS_PATH, json=payload)
            response.raise_for_status()
            parsed = _EmbeddingResponse.model_validate(response.json())
        except httpx.HTTPStatusError as error:
            raise self._error(
                f"Le service d'inférence a répondu {error.response.status_code}.",
                status_code=error.response.status_code,
            ) from error
        except httpx.HTTPError as error:
            raise self._error(f"Service d'inférence injoignable : {error}.") from error
        except (PydanticValidationError, ValueError) as error:
            raise self._error("Réponse illisible du service d'inférence.") from error
        return self._ordered_vectors(parsed, expected=len(texts))

    def _ordered_vectors(self, parsed: _EmbeddingResponse, *, expected: int) -> list[list[float]]:
        """Réordonne les vecteurs par `index` et vérifie la cohérence du lot."""
        if len(parsed.data) != expected:
            raise self._error(
                f"Le service d'inférence a renvoyé {len(parsed.data)} vecteurs "
                f"pour {expected} textes."
            )
        vectors = [item.embedding for item in sorted(parsed.data, key=lambda item: item.index)]
        if any(len(vector) != self.capabilities.dimensions for vector in vectors):
            raise self._error(
                f"Dimension inattendue : le modèle « {self.capabilities.provider_id} » "
                f"est déclaré à {self.capabilities.dimensions}."
            )
        return vectors

    def _error(self, detail: str, *, status_code: int | None = None) -> EmbeddingProviderError:
        """Assemble l'erreur de port, en nommant le fournisseur fautif."""
        return EmbeddingProviderError(
            detail,
            extensions={"provider_id": self.capabilities.provider_id, "status_code": status_code},
        )
