"""`GET /api/v1/knowledge-bases/{kb_id}/documents` — filtres `status`/`theme`.

Audit dette technique 2026-09, motif A : trois écrans du frontend tronquaient
cette liste à une page (50 documents) puis filtraient/comptaient dessus côté
client, rendant tout document au-delà invisible, non filtrable ou non
sélectionnable, sans le moindre message. Le correctif retenu est un filtre
serveur — ces tests verrouillent le contrat HTTP dont dépend `documents.ts`.

Poser `theme`/`status` directement en base après l'ingestion, comme le fait
déjà `test_document_chunks_routes.py::mark_indexed` : aucun de ces deux champs
n'a besoin du pipeline d'ingestion réel pour être exercé ici.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from http import HTTPStatus

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document
from app.domain.enums import DocumentStatus
from tests.api.v1.test_knowledge_base_routes import KB_URL, create_base, ingest, signed_in


async def _set_theme_and_status(
    db_session: AsyncSession,
    document_id: str,
    *,
    theme: str | None = None,
    status: DocumentStatus | None = None,
    created_at: datetime | None = None,
) -> None:
    document = await db_session.get(Document, uuid.UUID(document_id))
    assert document is not None
    if theme is not None:
        document.theme = theme
    if status is not None:
        document.status = status
    if created_at is not None:
        document.created_at = created_at
    await db_session.flush()


async def test_listing_documents_filters_by_status(
    crud_client: AsyncClient, db_session: AsyncSession
) -> None:
    await signed_in(crud_client)
    base = await create_base(crud_client)
    indexed = (await ingest(crud_client, base["id"], "indexe.pdf")).json()
    pending = (await ingest(crud_client, base["id"], "en-attente.pdf")).json()
    await _set_theme_and_status(db_session, indexed["id"], status=DocumentStatus.INDEXED)

    response = await crud_client.get(
        f"{KB_URL}/{base['id']}/documents", params={"status": "indexed"}
    )

    assert response.status_code == HTTPStatus.OK
    ids = [item["id"] for item in response.json()["data"]]
    assert ids == [indexed["id"]]
    assert pending["id"] not in ids


async def test_listing_documents_filters_by_theme(
    crud_client: AsyncClient, db_session: AsyncSession
) -> None:
    await signed_in(crud_client)
    base = await create_base(crud_client)
    produit = (await ingest(crud_client, base["id"], "produit.pdf")).json()
    autre = (await ingest(crud_client, base["id"], "autre.pdf")).json()
    await _set_theme_and_status(db_session, produit["id"], theme="produit")
    await _set_theme_and_status(db_session, autre["id"], theme="rh")

    response = await crud_client.get(
        f"{KB_URL}/{base['id']}/documents", params={"theme": "produit"}
    )

    assert response.status_code == HTTPStatus.OK
    ids = [item["id"] for item in response.json()["data"]]
    assert ids == [produit["id"]]
    assert autre["id"] not in ids


async def test_a_document_beyond_the_first_page_is_reachable_by_status_filter(
    crud_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Le cas exact de l'audit : un document au-delà de `limit` doit rester atteignable.

    Deux documents, page limitée à 1 : sans filtre serveur, le second document
    (le plus ancien) n'apparaît que sur la page suivante. Filtré par son statut,
    il doit être renvoyé directement, quelle que soit sa position dans le
    classement par date.
    """
    await signed_in(crud_client)
    base = await create_base(crud_client)
    recent = (await ingest(crud_client, base["id"], "recent.pdf")).json()
    older = (await ingest(crud_client, base["id"], "plus-ancien.pdf")).json()
    # Tri par `created_at desc` : deux insertions immédiatement successives
    # peuvent partager le même horodatage, auquel cas le départage par `id`
    # (aléatoire, UUID) rendrait l'ordre non déterministe. Un horodatage
    # explicitement antérieur lève l'ambiguïté sans dépendre du minuteur.
    await _set_theme_and_status(
        db_session,
        older["id"],
        status=DocumentStatus.INDEXED,
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    first_page = await crud_client.get(f"{KB_URL}/{base['id']}/documents", params={"limit": 1})
    assert [item["id"] for item in first_page.json()["data"]] == [recent["id"]]
    assert first_page.json()["next_cursor"] is not None

    filtered = await crud_client.get(
        f"{KB_URL}/{base['id']}/documents", params={"limit": 1, "status": "indexed"}
    )
    assert [item["id"] for item in filtered.json()["data"]] == [older["id"]]
