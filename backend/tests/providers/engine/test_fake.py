"""Contrat du moteur factice : il respecte tout le port, états et erreurs compris."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.domain.enums import JobState
from app.providers.engine import ingestion_engine_status, resolve_ingestion_engine
from app.providers.engine.base import (
    ChunkingProfile,
    EngineDatasetRef,
    EngineDocumentRef,
    EngineError,
    EngineSearchResult,
    EngineValidationError,
    IngestionEnginePort,
)
from app.providers.engine.fake import (
    NOT_TEXT_MESSAGE,
    FakeIngestionEngine,
    reset_shared_fake_engine,
    shared_fake_engine,
    split_into_chunks,
)

TEXT = (
    "Le délai de remboursement est de trente jours.\n\n"
    "La garantie couvre les défauts de fabrication.\n\n"
    "Résiliation : préavis de deux mois."
)


async def _indexed(
    engine: FakeIngestionEngine, content: bytes = TEXT.encode(), *, seed: str = "kb-1"
) -> tuple[EngineDatasetRef, EngineDocumentRef]:
    dataset = await engine.ensure_dataset(
        ref=None, name_seed=seed, embedding_provider_id="bge-m3", reranker_provider_id=None
    )
    document = await engine.upload_document(dataset, filename="notes.txt", content=content)
    await engine.start_parsing(dataset, document, ChunkingProfile())
    return dataset, document


def test_the_fake_engine_satisfies_the_port() -> None:
    assert isinstance(FakeIngestionEngine(), IngestionEnginePort)


async def test_ensure_dataset_is_idempotent_on_the_name_seed() -> None:
    engine = FakeIngestionEngine()

    first = await engine.ensure_dataset(
        ref=None, name_seed="kb-1", embedding_provider_id="bge-m3", reranker_provider_id=None
    )
    again = await engine.ensure_dataset(
        ref=None, name_seed="kb-1", embedding_provider_id="bge-m3", reranker_provider_id=None
    )
    other = await engine.ensure_dataset(
        ref=None, name_seed="kb-2", embedding_provider_id="bge-m3", reranker_provider_id=None
    )
    known = await engine.ensure_dataset(
        ref=first, name_seed="kb-9", embedding_provider_id="bge-m3", reranker_provider_id=None
    )

    assert again == first == known
    assert other != first


async def test_a_document_goes_from_unstart_to_done_through_the_phases() -> None:
    engine = FakeIngestionEngine(polls_before_done=3)
    dataset = await engine.ensure_dataset(
        ref=None, name_seed="kb", embedding_provider_id="bge-m3", reranker_provider_id=None
    )
    document = await engine.upload_document(dataset, filename="a.txt", content=TEXT.encode())

    assert (await engine.get_parse_status(dataset, document)).run == "unstart"
    await engine.start_parsing(dataset, document, ChunkingProfile())
    statuses = [await engine.get_parse_status(dataset, document) for _ in range(4)]

    assert [status.run for status in statuses] == ["running", "running", "running", "done"]
    assert [status.inferred_phase for status in statuses] == [
        JobState.CHUNKING,
        JobState.EMBEDDING,
        JobState.INDEXING,
        JobState.COMPLETED,
    ]
    assert statuses[-1].chunk_count == 3
    assert statuses[-1].is_terminal
    assert not statuses[0].is_terminal


async def test_a_binary_file_fails_to_parse_with_an_explicit_error() -> None:
    engine = FakeIngestionEngine()
    dataset, document = await _indexed(engine, b"\xff\xfe\x00binaire")

    status = await engine.get_parse_status(dataset, document)

    assert status.run == "fail"
    assert status.error == NOT_TEXT_MESSAGE


async def test_a_scripted_parse_failure_is_reported_as_fail() -> None:
    engine = FakeIngestionEngine()
    engine.parse_failure = "Panne simulée."
    _, document = await _indexed(engine)
    dataset = EngineDatasetRef(external_id="fake-dataset-1")

    status = await engine.get_parse_status(dataset, document)

    assert (status.run, status.error) == ("fail", "Panne simulée.")


async def test_cancelling_a_running_document_ends_in_cancel() -> None:
    engine = FakeIngestionEngine(polls_before_done=5)
    dataset, document = await _indexed(engine)

    await engine.cancel_parsing(dataset, document)
    status = await engine.get_parse_status(dataset, document)

    assert status.run == "cancel"
    assert status.is_terminal
    assert engine.cancellations == 1


async def test_search_is_lexical_ranked_and_ignores_accents_and_case() -> None:
    engine = FakeIngestionEngine()
    dataset, document = await _indexed(engine)
    await engine.get_parse_status(dataset, document)

    results = await engine.search(
        [dataset],
        query="DELAI de remboursement",
        top_k=5,
        rerank_provider_id=None,
        similarity_threshold=None,
    )

    # Les autres fragments ne partagent que « de » : classés derrière, à score partiel.
    assert results[0].content == "Le délai de remboursement est de trente jours."
    assert [r.similarity for r in results] == sorted((r.similarity for r in results), reverse=True)
    assert results[1].similarity == pytest.approx(1 / 3)
    assert results[0].document_external_id == document.external_id
    assert results[0].term_similarity == results[0].similarity == pytest.approx(1.0)
    assert results[0].vector_similarity == 0.0


async def test_search_honours_top_k_threshold_and_document_filter() -> None:
    engine = FakeIngestionEngine()
    dataset, first = await _indexed(engine)
    second = await engine.upload_document(
        dataset, filename="b.txt", content="jours fériés".encode()
    )
    await engine.start_parsing(dataset, second, ChunkingProfile())
    for document in (first, second):
        await engine.get_parse_status(dataset, document)

    async def search(
        query: str = "jours",
        *,
        threshold: float | None = None,
        documents: list[EngineDocumentRef] | None = None,
    ) -> list[EngineSearchResult]:
        return await engine.search(
            [dataset],
            query=query,
            top_k=1,
            rerank_provider_id=None,
            similarity_threshold=threshold,
            document_refs=documents,
        )

    assert len(await search()) == 1
    assert [r.document_external_id for r in await search(documents=[second])] == [
        second.external_id
    ]
    assert await search(documents=[]) == []
    assert await search("jours fériés absents", threshold=0.9) == []


async def test_search_rejects_an_out_of_range_top_k_and_an_unknown_dataset() -> None:
    engine = FakeIngestionEngine()
    dataset, _ = await _indexed(engine)

    with pytest.raises(EngineValidationError):
        await engine.search(
            [dataset], query="x", top_k=0, rerank_provider_id=None, similarity_threshold=None
        )
    with pytest.raises(EngineError):
        await engine.search(
            [EngineDatasetRef(external_id="inconnu")],
            query="x",
            top_k=1,
            rerank_provider_id=None,
            similarity_threshold=None,
        )
    assert (
        await engine.search(
            [], query="x", top_k=1, rerank_provider_id=None, similarity_threshold=None
        )
        == []
    )


async def test_list_document_chunks_paginates() -> None:
    engine = FakeIngestionEngine()
    dataset, document = await _indexed(engine)
    await engine.get_parse_status(dataset, document)

    first = await engine.list_document_chunks(dataset, document, page=1, page_size=2)
    second = await engine.list_document_chunks(dataset, document, page=2, page_size=2)

    assert (len(first.chunks), len(second.chunks), first.total) == (2, 1, 3)
    assert first.document.chunk_method == "naive"
    assert first.document.chunk_count == 3


async def test_deleting_is_not_idempotent_and_unknown_refs_are_errors() -> None:
    engine = FakeIngestionEngine()
    dataset, document = await _indexed(engine)

    await engine.delete_document(dataset, document)
    with pytest.raises(EngineError):
        await engine.delete_document(dataset, document)
    await engine.delete_dataset(dataset)
    with pytest.raises(EngineError):
        await engine.delete_dataset(dataset)
    with pytest.raises(EngineError):
        await engine.upload_document(dataset, filename="a.txt", content=b"x")


async def test_a_lenient_engine_adopts_unknown_references() -> None:
    engine = FakeIngestionEngine(strict=False)
    dataset = EngineDatasetRef(external_id="venu-de-la-base")
    document = EngineDocumentRef(external_id="doc")

    await engine.delete_document(dataset, document)
    await engine.delete_dataset(dataset)

    assert engine.documents_deleted == [("venu-de-la-base", "doc")]
    assert engine.datasets_deleted == ["venu-de-la-base"]


async def test_a_scripted_failure_is_raised_by_the_named_method() -> None:
    engine = FakeIngestionEngine()
    engine.failures["search"] = EngineError("panne")

    with pytest.raises(EngineError, match="panne"):
        await engine.search(
            [], query="x", top_k=1, rerank_provider_id=None, similarity_threshold=None
        )


def test_long_paragraphs_are_cut_at_whitespace() -> None:
    chunks = split_into_chunks("mot " * 600)

    assert len(chunks) == 3
    assert all(len(chunk) <= 1000 and not chunk.endswith(" ") for chunk in chunks)


def test_the_factory_resolves_the_fake_engine_as_a_process_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGESTION_ENGINE", "fake")
    reset_shared_fake_engine()
    settings = Settings()

    assert resolve_ingestion_engine(settings) is resolve_ingestion_engine(settings)
    assert resolve_ingestion_engine(settings) is shared_fake_engine()
    reset_shared_fake_engine()


def test_the_factory_defaults_to_ragflow_and_announces_the_fake_as_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = ingestion_engine_status(Settings())
    monkeypatch.setenv("INGESTION_ENGINE", "fake")
    fake = ingestion_engine_status(Settings())

    assert (default.provider_id, default.is_fake) == ("ragflow", False)
    assert (fake.provider_id, fake.is_fake, fake.available) == ("fake", True, True)


def test_ragflow_without_a_key_is_an_engine_error() -> None:
    with pytest.raises(EngineError, match="RAGFLOW_API_KEY"):
        resolve_ingestion_engine(Settings())
