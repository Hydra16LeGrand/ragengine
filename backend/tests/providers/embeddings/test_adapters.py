"""Adaptateurs d'embedding réseau : construction et gardes locales, sans trafic.

Ni le service d'inférence GPU ni l'API OpenAI ne doivent être appelés depuis la
suite de tests : elle doit rester verte sans réseau, sans GPU et sans clé d'API.
Ces adaptateurs ne sont donc éprouvés ici que sur ce qui ne dépend d'aucun tiers.
Là où un client est nécessaire, il est remplacé par un transport qui **échoue si
on l'utilise** — la preuve qu'aucun octet ne part.

La conformité au contrat HTTP réel de TEI (ADR-051 ; Xinference jusqu'à ADR-011)
relève d'une vérification empirique menée à la main contre une instance démarrée,
pas de cette suite : un test qui exigerait un GPU ne serait pas rejouable en CI.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.embeddings.base import EmbeddingPort
from app.providers.embeddings.local import BGE_M3_CAPABILITIES, LocalEmbeddingAdapter
from app.providers.embeddings.openai import (
    TEXT_EMBEDDING_3_LARGE_CAPABILITIES,
    OpenAIEmbeddingAdapter,
)


def exploding_client() -> httpx.AsyncClient:
    """Client HTTP dont toute requête est une erreur de test, jamais un appel."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"appel réseau interdit dans les tests : {request.url}")

    return httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="http://inference.invalid"
    )


def test_local_adapter_is_built_from_settings_alone() -> None:
    """Aucune URL en dur : tout vient de `Settings` (CONTRIBUTING.md §6)."""
    adapter = LocalEmbeddingAdapter.from_settings(Settings())

    assert isinstance(adapter, EmbeddingPort)
    assert adapter.capabilities == BGE_M3_CAPABILITIES
    assert adapter.capabilities.is_local is True


async def test_local_adapter_short_circuits_an_empty_batch() -> None:
    """Rien à vectoriser, donc rien à demander au service d'inférence."""
    client = exploding_client()
    adapter = LocalEmbeddingAdapter(base_url="http://inference.invalid", client=client)

    try:
        assert await adapter.embed_documents([]) == []
    finally:
        await client.aclose()


def test_openai_adapter_refuses_to_build_without_a_key() -> None:
    """Faute de configuration, pas panne de tiers : 422, pas 502."""
    with pytest.raises(ValidationError) as raised:
        OpenAIEmbeddingAdapter.from_settings(Settings())

    assert raised.value.status == 422


def test_openai_adapter_declares_the_catalogued_capabilities() -> None:
    """3072 dimensions : la valeur passée explicitement à l'API doit être celle annoncée."""
    adapter = OpenAIEmbeddingAdapter.from_settings(
        Settings(openai_api_key=SecretStr("sk-test-jamais-appelee"))
    )

    assert isinstance(adapter, EmbeddingPort)
    assert adapter.capabilities == TEXT_EMBEDDING_3_LARGE_CAPABILITIES
    assert adapter.capabilities.dimensions == 3072
