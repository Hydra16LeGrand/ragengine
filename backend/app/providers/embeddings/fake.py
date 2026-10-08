"""Adaptateur d'embedding déterministe, pour les tests d'orchestration.

Aucun réseau, aucun modèle : le vecteur d'un texte est dérivé de son empreinte
SHA-256. Deux propriétés en découlent, et ce sont elles qui rendent le fake
utile aux tests des services :

- **déterminisme** — le même texte donne toujours le même vecteur, d'une session
  à l'autre (contrairement à un tirage pseudo-aléatoire même graine, qui dépend
  de l'ordre des appels) ;
- **symétrie** — `embed_query(t)` est exactement `embed_documents([t])[0]`, ce
  qui permet d'écrire un test de similarité sans connaître le modèle.

Les vecteurs sont normalisés (norme L2 = 1) : un produit scalaire vaut donc une
similarité cosinus, ce qu'attend tout test de retrieval.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Final

from app.providers.embeddings.base import EmbeddingCapabilities

DEFAULT_FAKE_PROVIDER_ID: Final = "fake-embedding"
DEFAULT_FAKE_DIMENSIONS: Final = 8
"""Dimension volontairement petite : un test lit un vecteur, il ne l'indexe pas.

Un test qui a besoin de la parité avec le modèle local passe `dimensions=1024`.
"""

_BYTES_PER_DIGEST: Final = 32


class FakeEmbeddingAdapter:
    """Implémentation déterministe du port `EmbeddingPort`."""

    def __init__(
        self,
        *,
        dimensions: int = DEFAULT_FAKE_DIMENSIONS,
        provider_id: str = DEFAULT_FAKE_PROVIDER_ID,
    ) -> None:
        self.capabilities: EmbeddingCapabilities = EmbeddingCapabilities(
            provider_id=provider_id,
            dimensions=dimensions,
            max_batch_size=1024,
            max_input_tokens=1024,
            languages=None,
            is_local=False,
        )

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectorise chaque passage indépendamment, dans l'ordre reçu."""
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        """Vectorise une requête — strictement identique au passage correspondant."""
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        """Projette l'empreinte du texte sur la dimension déclarée, puis normalise."""
        dimensions = self.capabilities.dimensions
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        for block in range(math.ceil(dimensions / _BYTES_PER_DIGEST)):
            digest = hashlib.sha256(seed + block.to_bytes(4, "big")).digest()
            # Chaque octet donne une coordonnée dans [-1, 1].
            values.extend(byte / 127.5 - 1.0 for byte in digest)
        values = values[:dimensions]
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0.0:  # pragma: no cover - impossible avec SHA-256, garde-fou
            return values
        return [value / norm for value in values]
