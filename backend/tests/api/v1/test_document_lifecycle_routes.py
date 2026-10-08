"""Cycle de vie d'un document déjà versé : restitution du fichier, puis suppression.

Deux contrats se vérifient ici, et aucun des deux ne se voit depuis la couche
service :

- `GET …/documents/{id}/content` rend les **octets d'origine** avec les en-têtes
  qui décident de ce que le navigateur en fait. Le mode d'ouverture est une
  question de sécurité, pas d'ergonomie : `RAGENGINE_UPLOAD_ALLOWED_CONTENT_TYPES`
  accepte `text/html`, et un tel document servi `inline` s'exécuterait dans
  l'origine de l'application, avec son cookie de session.
- `DELETE …/documents/{id}` efface aussi les octets du volume. Le stockage étant
  monté ici sur un répertoire jetable **réel** (`test_storage`), l'effacement est
  constaté sur le disque, pas sur un double en mémoire.

Le moteur d'ingestion n'est jamais sollicité : aucun de ces documents n'atteint
le chunking, donc aucun ne porte de `ragflow_document_external_id`, et
`DocumentService.delete` n'a rien à retirer côté RAGFlow.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from httpx import AsyncClient

from app.main import API_PREFIX
from app.providers.storage import LocalFilesystemStorageAdapter
from app.providers.storage.base import StorageError
from tests.api.v1.test_knowledge_base_routes import (
    KB_URL,
    create_base,
    file_bytes,
    ingest,
    signed_in,
)

DOCUMENTS_URL = f"{API_PREFIX}/documents"


async def ingest_named(
    client: AsyncClient, base_id: str, filename: str, content_type: str
) -> dict[str, Any]:
    """Verse un fichier sous un nom et un type donnés, et renvoie le document créé."""
    response = await client.post(
        f"{KB_URL}/{base_id}/documents/ingest",
        files={"file": (filename, file_bytes(filename), content_type)},
    )
    assert response.status_code == HTTPStatus.ACCEPTED, response.text
    return dict(response.json())


async def test_the_content_route_returns_the_uploaded_bytes(crud_client: AsyncClient) -> None:
    """Aller-retour complet : ce qui sort est exactement ce qui est entré."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()

    response = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}/content")

    assert response.status_code == HTTPStatus.OK
    assert response.content == file_bytes("rapport.pdf")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="rapport.pdf"'
    # Sans ce garde-fou, le navigateur pourrait renifler un autre type que celui
    # annoncé, et rendre en HTML un fichier servi `inline`.
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_a_non_ascii_filename_is_encoded_in_the_disposition(
    crud_client: AsyncClient,
) -> None:
    """Corpus francophone : un accent ne doit ni casser l'en-tête, ni perdre le nom."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = await ingest_named(crud_client, base["id"], "rapport qualité.pdf", "application/pdf")

    response = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}/content")

    assert response.status_code == HTTPStatus.OK
    disposition = response.headers["content-disposition"]
    assert disposition == "inline; filename*=utf-8''rapport%20qualit%C3%A9.pdf"


async def test_an_html_document_is_served_as_an_attachment(crud_client: AsyncClient) -> None:
    """`text/html` est ingérable mais jamais rendu : il s'exécuterait dans notre origine."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = await ingest_named(crud_client, base["id"], "page.html", "text/html")
    assert document["content_type"] == "text/html"

    response = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}/content")

    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-disposition"].startswith("attachment;")


async def test_the_content_of_an_invisible_document_answers_not_found(
    crud_client: AsyncClient,
) -> None:
    """Même règle que la lecture du document : la visibilité est celle de sa base."""
    await signed_in(crud_client, "owner")
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()

    # Le cookie du second compte remplace celui du premier.
    await signed_in(crud_client, "intruder")
    response = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}/content")

    assert response.status_code == HTTPStatus.NOT_FOUND


async def test_deleting_a_document_erases_its_bytes_from_the_volume(
    crud_client: AsyncClient, test_storage: LocalFilesystemStorageAdapter
) -> None:
    """La ligne et le fichier partent ensemble : plus d'orphelin sur le volume."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    storage_uri = document["storage_uri"]
    assert await test_storage.read(storage_uri) == file_bytes("rapport.pdf")

    deleted = await crud_client.delete(f"{DOCUMENTS_URL}/{document['id']}")

    assert deleted.status_code == HTTPStatus.NO_CONTENT
    with pytest.raises(StorageError):
        await test_storage.read(storage_uri)
    gone = await crud_client.get(f"{DOCUMENTS_URL}/{document['id']}")
    assert gone.status_code == HTTPStatus.NOT_FOUND


async def test_deleting_a_document_decrements_the_counter_of_its_base(
    crud_client: AsyncClient,
) -> None:
    """Le compteur dénormalisé suit la suppression comme il suit le versement."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()

    await crud_client.delete(f"{DOCUMENTS_URL}/{document['id']}")

    reloaded = await crud_client.get(f"{KB_URL}/{base['id']}")
    assert reloaded.json()["document_count"] == 0


async def test_patching_a_document_sets_its_theme(crud_client: AsyncClient) -> None:
    """`PATCH` pose un thème, saisi par l'utilisateur — absent tant que non posé."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    assert document["theme"] is None

    response = await crud_client.patch(
        f"{DOCUMENTS_URL}/{document['id']}", json={"theme": "juridique"}
    )

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["theme"] == "juridique"


async def test_patching_a_document_cannot_clear_the_filename(crud_client: AsyncClient) -> None:
    """`filename: null` est refusé (422) — contrairement à `theme`, non vidable."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()

    response = await crud_client.patch(f"{DOCUMENTS_URL}/{document['id']}", json={"filename": None})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
