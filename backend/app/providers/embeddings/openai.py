"""Adaptateur d'embedding OpenAI — `text-embedding-3-large`, via le SDK officiel.

Seul ce module importe `openai` côté embeddings (CONTRIBUTING.md §3.2). Les exceptions
du SDK n'en sortent jamais : elles sont traduites en `EmbeddingProviderError`,
sauf l'absence de clé — une faute de configuration, pas une panne de tiers, donc
`ValidationError` (422).

Le paramètre `dimensions` est **passé explicitement** à l'API : les modèles v3
savent tronquer leurs vecteurs, et laisser le défaut implicite ferait diverger en
silence la taille réelle de celle annoncée par `capabilities` — donc de la
colonne d'index d'une base de connaissances.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import batched
from typing import Final

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, OpenAIError, RateLimitError
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.embeddings.base import (
    EmbeddingCapabilities,
    EmbeddingProviderError,
)

TEXT_EMBEDDING_3_LARGE_CAPABILITIES: Final = EmbeddingCapabilities(
    provider_id="text-embedding-3-large",
    dimensions=3072,
    # Plafond documenté par OpenAI pour un appel `embeddings.create`. Contrairement
    # au local, aucun budget VRAM ne s'y applique : `settings.embedding_batch_size`
    # est une contrainte du service GPU (CONTRIBUTING.md §2), pas d'une API tierce.
    max_batch_size=2048,
    max_input_tokens=8191,
    languages=None,
    is_local=False,
)
"""Capacités du modèle d'embedding OpenAI enregistré au catalogue."""

_MISSING_API_KEY: Final = (
    "OPENAI_API_KEY n'est pas configurée : le fournisseur d'embeddings "
    "« text-embedding-3-large » est indisponible."
)


class OpenAIEmbeddingAdapter:
    """Client `AsyncOpenAI` conforme au port `EmbeddingPort`."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        capabilities: EmbeddingCapabilities = TEXT_EMBEDDING_3_LARGE_CAPABILITIES,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.capabilities: EmbeddingCapabilities = capabilities
        self._api_key = api_key
        # Comme pour l'adaptateur local : construire un adaptateur n'alloue
        # aucune ressource, le client naît au premier appel réel.
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIEmbeddingAdapter:
        """Construit l'adaptateur depuis la configuration.

        Le registre filtre déjà les fournisseurs sans clé ; la garde est
        conservée pour qu'une construction directe échoue clairement.
        """
        if settings.openai_api_key is None:
            raise ValidationError(_MISSING_API_KEY)
        return cls(api_key=settings.openai_api_key)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectorise des passages, lot par lot, en préservant l'ordre d'entrée."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for batch in batched(texts, self.capabilities.max_batch_size):
            vectors.extend(await self._embed(list(batch)))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Vectorise une requête.

        Les modèles OpenAI sont symétriques : requête et passage passent par le
        même appel. Le port garde les deux méthodes séparées pour les familles
        qui ne le sont pas (`docs/providers-architecture.md` §1).
        """
        vectors = await self._embed([text])
        return vectors[0]

    async def aclose(self) -> None:
        """Ferme le client sous-jacent s'il a été créé."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _openai_client(self) -> AsyncOpenAI:
        """Renvoie le client SDK, créé paresseusement au premier appel réseau."""
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._api_key.get_secret_value())
        return self._client

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Exécute un appel, et traduit toute exception du SDK."""
        try:
            response = await self._openai_client().embeddings.create(
                model=self.capabilities.provider_id,
                input=texts,
                dimensions=self.capabilities.dimensions,
            )
        except RateLimitError as error:
            raise self._error("Quota OpenAI dépassé.", status_code=error.status_code) from error
        except APIStatusError as error:
            raise self._error(
                f"OpenAI a répondu {error.status_code}.", status_code=error.status_code
            ) from error
        except APIConnectionError as error:
            raise self._error(f"API OpenAI injoignable : {error}.") from error
        except OpenAIError as error:
            # Filet : aucune exception du SDK ne quitte l'adaptateur (§0).
            raise self._error(f"Appel OpenAI en échec : {error}.") from error
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    def _error(self, detail: str, *, status_code: int | None = None) -> EmbeddingProviderError:
        """Assemble l'erreur de port, en nommant le fournisseur fautif."""
        return EmbeddingProviderError(
            detail,
            extensions={"provider_id": self.capabilities.provider_id, "status_code": status_code},
        )
