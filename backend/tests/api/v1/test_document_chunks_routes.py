"""`GET /api/v1/documents/{document_id}/chunks` (Phase 13, consultation des fragments).

Le moteur d'ingestion est substitué sur son point de résolution dans
`app.services.documents` — même technique que `test_retrieval.py`/
`test_evaluation_routes.py` (`resolve_ingestion_engine`) : le routeur construit
`DocumentService` directement, aucune dépendance FastAPI à surcharger.

Un document n'atteint le chunking qu'après le pipeline d'ingestion complet
(precheck → decision → chunking → embedding → indexation), jamais rejoué ici :
les deux identifiants moteur (`ragflow_dataset_external_id`,
`ragflow_document_external_id`) sont posés directement en base, comme le fait
déjà `test_evaluation_routes.py` pour les mêmes raisons.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, KnowledgeBase
from app.domain.enums import DocumentStatus
from app.main import API_PREFIX
from app.providers.engine.base import (
    EngineChunk,
    EngineChunkPosition,
    EngineDocumentChunksMeta,
    EngineDocumentChunksPage,
)
from app.providers.engine.fake import FakeIngestionEngine
from app.services import documents as documents_service
from tests.api.v1.test_knowledge_base_routes import (
    KB_URL,
    create_base,
    ingest,
    log_in,
    register,
    signed_in,
)

DOCUMENTS_URL = f"{API_PREFIX}/documents"


def chunk_engine(*, page_to_return: EngineDocumentChunksPage) -> FakeIngestionEngine:
    """Moteur dont `list_document_chunks` rend toujours `page_to_return`."""
    engine = FakeIngestionEngine(strict=False)
    engine.chunk_pages = [page_to_return]
    return engine


def default_page(**overrides: object) -> EngineDocumentChunksPage:
    """Page moteur par défaut : un fragment complet, réutilisée par la majorité des tests."""
    defaults: dict[str, object] = {
        "chunks": [
            EngineChunk(
                chunk_id="chunk-1",
                content="Un extrait du document.",
                positions=[EngineChunkPosition(page=2, x0=10, x1=90, top=20, bottom=40)],
                important_keywords=["remboursement"],
                questions=["Quel est le délai de remboursement ?"],
                available=True,
            )
        ],
        "document": EngineDocumentChunksMeta(chunk_method="naive", chunk_count=1, token_count=42),
        "total": 1,
        "page": 1,
        "page_size": 20,
    }
    defaults.update(overrides)
    return EngineDocumentChunksPage(**defaults)  # type: ignore[arg-type]


def install_engine(
    monkeypatch: pytest.MonkeyPatch, page: EngineDocumentChunksPage
) -> FakeIngestionEngine:
    """Substitue la résolution du moteur, comme les autres suites d'API."""
    engine = chunk_engine(page_to_return=page)
    monkeypatch.setattr(documents_service, "resolve_ingestion_engine", lambda settings: engine)
    return engine


async def mark_indexed(
    db_session: AsyncSession,
    document_id: str,
    *,
    dataset_external_id: str = "dataset-externe",
    document_external_id: str = "document-externe",
) -> None:
    """Pose les deux identifiants moteur, comme le ferait le chunking réel une fois abouti."""
    document = await db_session.get(Document, uuid.UUID(document_id))
    assert document is not None
    knowledge_base = await db_session.get(KnowledgeBase, document.knowledge_base_id)
    assert knowledge_base is not None
    document.status = DocumentStatus.INDEXED
    document.ragflow_document_external_id = document_external_id
    knowledge_base.ragflow_dataset_external_id = dataset_external_id
    await db_session.flush()


async def test_listing_chunks_of_an_indexed_document_returns_them(
    crud_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aller-retour complet : contenu, page dérivée de `positions`, mots-clés, questions."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    await mark_indexed(db_session, document["id"])
    install_engine(monkeypatch, default_page())

    response = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}/chunks")

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert len(body["data"]) == 1
    chunk = body["data"][0]
    assert chunk["chunk_id"] == "chunk-1"
    assert chunk["content"] == "Un extrait du document."
    assert chunk["page_start"] == 2
    assert chunk["page_end"] == 2
    assert chunk["important_keywords"] == ["remboursement"]
    assert chunk["questions"] == ["Quel est le délai de remboursement ?"]
    assert body["document"] == {
        "chunk_method": "naive",
        "chunk_count": 1,
        "token_count": 42,
        # ADR-033 : le document d'essai n'a pas de décision de precheck en base,
        # donc pas de découpage parent/enfant — l'écran garde sa lecture d'avant.
        "uses_parent_child": False,
    }
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20


async def test_listing_chunks_forwards_pagination_query_params(
    crud_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`page`/`page_size` passés en requête atteignent le moteur tels quels."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    await mark_indexed(db_session, document["id"])
    engine = install_engine(monkeypatch, default_page(page=3, page_size=5))

    response = await crud_client.get(
        f"{DOCUMENTS_URL}/{document['id']}/chunks", params={"page": 3, "page_size": 5}
    )

    assert response.status_code == HTTPStatus.OK, response.text
    assert engine.chunk_list_calls == [("dataset-externe", "document-externe", 3, 5)]


async def test_listing_chunks_rejects_a_page_size_above_the_ragflow_ceiling(
    crud_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`page_size=101` : `422`, pas un appel moteur qui échouerait de façon confuse."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    await mark_indexed(db_session, document["id"])
    engine = install_engine(monkeypatch, default_page())

    response = await crud_client.get(
        f"{DOCUMENTS_URL}/{document['id']}/chunks", params={"page_size": 101}
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert engine.chunk_list_calls == []


async def test_listing_chunks_of_a_zero_chunk_document_is_an_empty_page(
    crud_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-024 : le garde-fou qualité peut avoir signalé zéro fragment — `200`, pas une erreur."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    await mark_indexed(db_session, document["id"])
    install_engine(
        monkeypatch,
        default_page(
            chunks=[],
            document=EngineDocumentChunksMeta(chunk_method="naive", chunk_count=0, token_count=0),
            total=0,
        ),
    )

    response = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}/chunks")

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["data"] == []
    assert body["total"] == 0
    assert body["document"]["chunk_count"] == 0


async def test_listing_chunks_of_a_document_not_yet_indexed_answers_not_found(
    crud_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un document versé mais jamais parvenu au chunking n'a aucune référence moteur."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    engine = install_engine(monkeypatch, default_page())

    response = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}/chunks")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert engine.chunk_list_calls == []


async def test_listing_chunks_of_an_invisible_document_answers_not_found_not_forbidden(
    crud_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Même règle que `read_document_content` : la visibilité est celle de la base, jamais `403`."""
    await signed_in(crud_client, "owner")
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    await mark_indexed(db_session, document["id"])
    install_engine(monkeypatch, default_page())

    # Le cookie du second compte remplace celui du premier.
    await signed_in(crud_client, "intruder")
    response = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}/chunks")

    assert response.status_code == HTTPStatus.NOT_FOUND


async def test_a_viewer_member_can_list_chunks(
    crud_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le rôle minimal `viewer` suffit — même seuil que le reste de la lecture de documents."""
    await signed_in(crud_client, "proprio")
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    await mark_indexed(db_session, document["id"])
    install_engine(monkeypatch, default_page())
    viewer = await register(crud_client, "lecteur")
    await crud_client.put(f"{KB_URL}/{base['id']}/members/{viewer['id']}", json={"role": "viewer"})
    await log_in(crud_client, viewer)

    response = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}/chunks")

    assert response.status_code == HTTPStatus.OK, response.text


async def test_parents_only_reaches_the_service_and_filters_the_default_fixture(
    crud_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`?parents_only=true` traverse le contrat HTTP — la logique de boucle/filtrage

    elle-même est éprouvée au niveau service (`tests/services/test_documents.py`),
    ce test-ci prouve seulement que le paramètre arrive jusque-là. Le fragment
    unique de `default_page()` est `available=True` (un enfant) : filtré,
    `total` tombe à `0` plutôt que le `1` obtenu sans le paramètre.
    """
    await signed_in(crud_client, "proprio")
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    await mark_indexed(db_session, document["id"])
    install_engine(monkeypatch, default_page())

    response = await crud_client.get(
        f"{DOCUMENTS_URL}/{document['id']}/chunks", params={"parents_only": "true"}
    )

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["total"] == 0
    assert body["data"] == []
