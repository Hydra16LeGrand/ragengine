"""Moteur factice à état partagé (ADR-071) : deux instances, une seule vérité, dans Redis."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
import redis as redis_sync

from app.core.config import Settings
from app.providers.engine import ingestion_engine_status, resolve_ingestion_engine
from app.providers.engine.base import (
    ChunkingProfile,
    EngineDatasetRef,
    EngineDocumentRef,
    EngineError,
)
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.engine.fake_shared import SharedStateFakeEngine

REDIS_URL = f"redis://127.0.0.1:{os.environ.get('RAGENGINE_REDIS_HOST_PORT', '6380')}/14"
TEXT = "Le délai de remboursement est de trente jours.\n\nLa garantie couvre les défauts."


@pytest.fixture
def keys() -> Iterator[tuple[str, str]]:
    """Clés propres au test ; Redis injoignable ⇒ skip."""
    client = redis_sync.Redis.from_url(REDIS_URL)
    try:
        client.ping()
    except redis_sync.RedisError:
        pytest.skip("Redis de développement injoignable.")
    suffix = uuid.uuid4().hex
    names = (f"test:fake-state:{suffix}", f"test:fake-lock:{suffix}")
    yield names
    client.delete(*names)


async def _upload(engine: FakeIngestionEngine, content: bytes) -> tuple[str, str]:
    dataset = await engine.ensure_dataset(
        ref=None, name_seed="kb", embedding_provider_id="bge-m3", reranker_provider_id=None
    )
    document = await engine.upload_document(dataset, filename="f.txt", content=content)
    return dataset.external_id, document.external_id


async def test_a_second_instance_sees_what_the_first_one_did(keys: tuple[str, str]) -> None:
    state_key, lock_key = keys
    api = SharedStateFakeEngine(REDIS_URL, state_key=state_key, lock_key=lock_key)
    worker = SharedStateFakeEngine(REDIS_URL, state_key=state_key, lock_key=lock_key)
    dataset_id, document_id = await _upload(api, TEXT.encode())
    dataset, document = (
        EngineDatasetRef(external_id=dataset_id),
        EngineDocumentRef(external_id=document_id),
    )

    # Le worker, une autre instance, lance puis sonde le parsing — l'API cherche ensuite.
    await worker.start_parsing(dataset, document, ChunkingProfile())
    status = await worker.get_parse_status(dataset, document)
    while status.run == "running":
        status = await worker.get_parse_status(dataset, document)
    assert status.run == "done"

    found = await api.search([dataset], query="délai de remboursement", top_k=3,
                             rerank_provider_id=None, similarity_threshold=None)  # fmt: skip
    assert found[0].content == "Le délai de remboursement est de trente jours."
    # Les identifiants ne se rejouent pas d'une instance à l'autre : le compteur est partagé.
    assert (await _upload(worker, b"autre"))[1] != document_id


async def test_two_state_keys_are_two_separate_engines(keys: tuple[str, str]) -> None:
    state_key, lock_key = keys
    first = SharedStateFakeEngine(REDIS_URL, state_key=state_key, lock_key=lock_key)
    other = SharedStateFakeEngine(
        REDIS_URL, state_key=state_key + ":autre", lock_key=lock_key + ":autre"
    )
    dataset_id, _ = await _upload(first, TEXT.encode())

    with pytest.raises(EngineError):
        await other.delete_dataset(EngineDatasetRef(external_id=dataset_id))


async def test_binary_content_survives_the_round_trip() -> None:
    engine = FakeIngestionEngine()
    _, document_id = await _upload(engine, b"\xff\x00\xfe binaire")
    restored = FakeIngestionEngine()
    restored.import_state(engine.export_state())

    assert restored.export_state() == engine.export_state()
    assert document_id in restored.export_state().decode()


def test_the_registry_resolves_the_shared_engine_and_announces_it_as_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGESTION_ENGINE", "fake-shared")
    settings = Settings()

    assert isinstance(resolve_ingestion_engine(settings), SharedStateFakeEngine)
    # Une instance neuve à chaque résolution : seul Redis relie les process.
    assert resolve_ingestion_engine(settings) is not resolve_ingestion_engine(settings)
    status = ingestion_engine_status(settings)
    assert (status.provider_id, status.is_fake, status.available) == ("fake-shared", True, True)
