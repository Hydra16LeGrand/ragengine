"""`FakeEmbeddingAdapter` : conformité au port et déterminisme.

Ces propriétés sont ce sur quoi s'appuieront les tests d'orchestration des
services : si le fake cesse d'être déterministe ou de respecter les dimensions
qu'il annonce, ce sont eux qui deviennent illisibles.
"""

from __future__ import annotations

import math

import pytest

from app.providers.embeddings.base import EmbeddingPort
from app.providers.embeddings.fake import DEFAULT_FAKE_DIMENSIONS, FakeEmbeddingAdapter

TEXTS = ["Le chat dort.", "Un rapport financier trimestriel.", "The quick brown fox."]


def test_fake_satisfies_the_embedding_port() -> None:
    """Le fake est substituable à tout adaptateur réel."""
    assert isinstance(FakeEmbeddingAdapter(), EmbeddingPort)


async def test_embed_documents_returns_one_vector_per_text() -> None:
    """La forme de sortie est `list[list[float]]`, alignée sur l'entrée."""
    adapter = FakeEmbeddingAdapter()

    vectors = await adapter.embed_documents(TEXTS)

    assert len(vectors) == len(TEXTS)
    assert all(isinstance(value, float) for vector in vectors for value in vector)


async def test_vectors_have_the_declared_dimensions() -> None:
    """La sortie respecte `capabilities.dimensions`, jamais une taille arbitraire."""
    adapter = FakeEmbeddingAdapter()

    vectors = await adapter.embed_documents(TEXTS)

    assert adapter.capabilities.dimensions == DEFAULT_FAKE_DIMENSIONS
    assert {len(vector) for vector in vectors} == {adapter.capabilities.dimensions}


@pytest.mark.parametrize("dimensions", [1, 8, 33, 1024])
async def test_dimensions_are_configurable(dimensions: int) -> None:
    """Un test qui a besoin de la parité avec BGE-M3 demande simplement 1024."""
    adapter = FakeEmbeddingAdapter(dimensions=dimensions)

    vector = await adapter.embed_query("requête")

    assert adapter.capabilities.dimensions == dimensions
    assert len(vector) == dimensions


async def test_the_same_text_always_yields_the_same_vector() -> None:
    """Déterminisme d'une instance à l'autre : aucun état, aucune graine implicite."""
    first = await FakeEmbeddingAdapter().embed_documents(TEXTS)
    second = await FakeEmbeddingAdapter().embed_documents(TEXTS)

    assert first == second


async def test_distinct_texts_yield_distinct_vectors() -> None:
    """Sans quoi un test de retrieval ne pourrait rien distinguer."""
    vectors = await FakeEmbeddingAdapter().embed_documents(TEXTS)

    assert len({tuple(vector) for vector in vectors}) == len(TEXTS)


async def test_query_and_document_share_the_same_representation() -> None:
    """Le fake est symétrique : `embed_query(t) == embed_documents([t])[0]`."""
    adapter = FakeEmbeddingAdapter()

    query_vector = await adapter.embed_query(TEXTS[0])
    document_vectors = await adapter.embed_documents([TEXTS[0]])

    assert query_vector == document_vectors[0]


async def test_vectors_are_l2_normalized() -> None:
    """Un produit scalaire vaut donc une similarité cosinus."""
    vectors = await FakeEmbeddingAdapter(dimensions=64).embed_documents(TEXTS)

    for vector in vectors:
        assert math.isclose(math.sqrt(sum(value**2 for value in vector)), 1.0, rel_tol=1e-9)


async def test_embedding_no_text_calls_nothing_and_returns_nothing() -> None:
    """Cas limite : un lot vide n'est pas une erreur."""
    assert await FakeEmbeddingAdapter().embed_documents([]) == []
