"""`LocalRerankerAdapter` : construction et gardes locales, sans trafic réseau.

La suite doit rester verte sans réseau ni GPU. Le client injecté ici échoue si
une requête l'atteint : les assertions qui suivent démontrent donc que les gardes
agissent **avant** tout appel.

La conformité au contrat HTTP réel de TEI (ADR-051 ; Xinference jusqu'à ADR-011)
relève d'une vérification empirique menée à la main contre une instance démarrée,
pas de cette suite : un test qui exigerait un GPU ne serait pas rejouable en CI.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.rerankers.base import RerankerPort
from app.providers.rerankers.local import (
    BGE_RERANKER_V2_M3_CAPABILITIES,
    LocalRerankerAdapter,
)

QUERY = "requête de test"


def exploding_client() -> httpx.AsyncClient:
    """Client HTTP dont toute requête est une erreur de test, jamais un appel."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"appel réseau interdit dans les tests : {request.url}")

    return httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="http://inference.invalid"
    )


def test_local_adapter_is_built_from_settings_alone() -> None:
    """Aucune URL en dur : tout vient de `Settings` (CONTRIBUTING.md §6)."""
    adapter = LocalRerankerAdapter.from_settings(Settings())

    assert isinstance(adapter, RerankerPort)
    assert adapter.capabilities == BGE_RERANKER_V2_M3_CAPABILITIES
    assert adapter.capabilities.is_local is True


async def test_empty_candidates_are_not_sent_to_the_inference_service() -> None:
    """Rien à reclasser, donc rien à demander."""
    client = exploding_client()
    adapter = LocalRerankerAdapter(base_url="http://inference.invalid", client=client)

    try:
        assert await adapter.rerank(QUERY, []) == []
    finally:
        await client.aclose()


async def test_a_batch_larger_than_the_capacity_is_refused_locally() -> None:
    """Le dépassement est un bug d'appelant : il échoue en 422, sans troncature muette."""
    client = exploding_client()
    adapter = LocalRerankerAdapter(base_url="http://inference.invalid", client=client)
    documents = ["passage"] * (BGE_RERANKER_V2_M3_CAPABILITIES.max_documents + 1)

    try:
        with pytest.raises(ValidationError) as raised:
            await adapter.rerank(QUERY, documents)
    finally:
        await client.aclose()

    assert raised.value.status == 422
