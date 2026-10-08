"""Parcours HTTP complets du CRUD : bases, membres, documents, jobs.

Ces tests décrivent le contrat tel que le frontend le consommera — codes de
statut, `type` des Problem Details, et surtout **la matrice §4 appliquée bout en
bout**, à travers de vraies requêtes et de vrais cookies : un `viewer` qui
n'ingère pas, un anonyme qui ne voit pas une base privée, un invité arrêté par
son quota."""

from __future__ import annotations

import hashlib
import uuid
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient, Response

from app.core.config import Settings, get_settings
from app.core.errors import PROBLEM_MEDIA_TYPE
from app.domain.schemas.common import MAX_FILENAME_LENGTH, MAX_THEME_LENGTH
from app.main import API_PREFIX
from app.providers.storage import LocalFilesystemStorageAdapter
from app.workers.celery_app import CHUNKING_TASK, PRECHECK_TASK
from tests.conftest import DispatchedTasks
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


KB_URL = f"{API_PREFIX}/knowledge-bases"


async def register(
    client: AsyncClient, prefix: str = "crud", *, display_name: str = "Amara"
) -> dict[str, Any]:
    """Inscrit un compte réel et renvoie sa représentation (identifiant compris)."""
    payload = {
        "email": factories.unique_email(prefix),
        "password": PASSWORD,
        "display_name": display_name,
    }
    response = await client.post(f"{API_PREFIX}/users", json=payload)
    assert response.status_code == HTTPStatus.CREATED
    return dict(response.json()) | {"password": PASSWORD}


async def log_in(client: AsyncClient, user: dict[str, Any]) -> None:
    """Ouvre une session pour `user` ; le cookie remplace celui du compte précédent."""
    response = await client.post(
        f"{API_PREFIX}/sessions", json={"email": user["email"], "password": user["password"]}
    )
    assert response.status_code == HTTPStatus.CREATED


async def signed_in(
    client: AsyncClient, prefix: str = "crud", *, display_name: str = "Amara"
) -> dict[str, Any]:
    """Inscrit un compte et le connecte immédiatement."""
    user = await register(client, prefix, display_name=display_name)
    await log_in(client, user)
    return user


def creation(**overrides: object) -> dict[str, Any]:
    """Corps de création de base valide."""
    payload: dict[str, object] = {"name": "Référentiel Qualité", "embedding_model_id": "bge-m3"}
    return payload | overrides


def file_bytes(marker: str = "rapport.pdf", *, size: int = 1024) -> bytes:
    """Contenu binaire distinct par document, pour que les empreintes diffèrent."""
    return (f"%PDF-1.4 {marker} ".encode() + b"x" * size)[:size]


def upload(filename: str = "rapport.pdf", *, size: int = 1024) -> dict[str, Any]:
    """Corps `multipart/form-data` d'une ingestion (ADR-014).

    Aucune métadonnée n'accompagne le fichier : `content_type`, `size_bytes` et
    `checksum_sha256` sont calculés côté serveur, et un client qui les enverrait
    n'y gagnerait rien.
    """
    return {"files": {"file": (filename, file_bytes(filename, size=size), "application/pdf")}}


async def ingest(
    client: AsyncClient, base_id: str, filename: str = "rapport.pdf", *, size: int = 1024
) -> Response:
    """Téléverse un document dans une base et renvoie la réponse brute."""
    return await client.post(f"{KB_URL}/{base_id}/documents/ingest", **upload(filename, size=size))


async def create_base(client: AsyncClient, **overrides: object) -> dict[str, Any]:
    """Crée une base par HTTP et renvoie sa représentation."""
    response = await client.post(KB_URL, json=creation(**overrides))
    assert response.status_code == HTTPStatus.CREATED, response.text
    return dict(response.json())


async def test_the_full_knowledge_base_lifecycle(crud_client: AsyncClient) -> None:
    """Création → lecture → mise à jour → liste → suppression en cascade."""
    await signed_in(crud_client)
    created = await create_base(crud_client)
    assert created["slug"] == "referentiel-qualite"
    assert created["document_count"] == 0

    ingested = await ingest(crud_client, created["id"])
    assert ingested.status_code == HTTPStatus.ACCEPTED

    read = await crud_client.get(f"{KB_URL}/{created['id']}")
    assert read.status_code == HTTPStatus.OK
    assert read.json()["document_count"] == 1

    updated = await crud_client.patch(
        f"{KB_URL}/{created['id']}", json={"name": "Qualité 2026", "visibility": "public"}
    )
    assert updated.status_code == HTTPStatus.OK
    assert updated.json()["name"] == "Qualité 2026"
    # Le slug survit au renommage : les liens déjà partagés restent valides.
    assert updated.json()["slug"] == "referentiel-qualite"

    listed = await crud_client.get(KB_URL)
    assert listed.status_code == HTTPStatus.OK
    assert [item["id"] for item in listed.json()["data"]] == [created["id"]]
    assert listed.json()["next_cursor"] is None

    deleted = await crud_client.delete(f"{KB_URL}/{created['id']}")
    assert deleted.status_code == HTTPStatus.NO_CONTENT

    document_id = ingested.json()["id"]
    orphan = await crud_client.get(f"{API_PREFIX}/documents/{document_id}")
    assert orphan.status_code == HTTPStatus.NOT_FOUND
    assert (await crud_client.get(KB_URL)).json()["data"] == []


async def test_creating_a_base_twice_under_the_same_slug_answers_409(
    crud_client: AsyncClient,
) -> None:
    """L'unicité `(owner_id, slug)` sort en Problem Details."""
    await signed_in(crud_client)
    await create_base(crud_client)

    response = await crud_client.post(KB_URL, json=creation())

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["type"] == "/problems/conflict"


async def test_creating_a_base_requires_authentication(crud_client: AsyncClient) -> None:
    """Une action persistante déclenche le parcours d'élévation."""
    response = await crud_client.post(KB_URL, json=creation())

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "/problems/authentication-required"


async def test_the_member_lifecycle(crud_client: AsyncClient) -> None:
    """Ajout (`201`) → changement de rôle (`200`) → lecture → retrait (`204`)."""
    await signed_in(crud_client, "proprio")
    base = await create_base(crud_client)
    guest_member = await register(crud_client, "membre")
    member_url = f"{KB_URL}/{base['id']}/members/{guest_member['id']}"

    added = await crud_client.put(member_url, json={"role": "viewer"})
    assert added.status_code == HTTPStatus.CREATED
    assert added.json()["role"] == "viewer"

    promoted = await crud_client.put(member_url, json={"role": "editor"})
    assert promoted.status_code == HTTPStatus.OK
    assert promoted.json()["role"] == "editor"

    read = await crud_client.get(member_url)
    assert read.status_code == HTTPStatus.OK
    assert read.json()["user_id"] == guest_member["id"]
    assert read.json()["display_name"] == "Amara"

    removed = await crud_client.delete(member_url)
    assert removed.status_code == HTTPStatus.NO_CONTENT
    assert (await crud_client.get(member_url)).status_code == HTTPStatus.NOT_FOUND


async def test_removing_the_last_owner_answers_409(crud_client: AsyncClient) -> None:
    """`docs/domain-model.md` §1 : une base garde toujours au moins un `owner`.

    L'écran « Membres et rôles » propose désormais le retrait sur **toute**
    ligne, y compris celle du créateur : c'est ce refus qui tient l'invariant, et
    son `detail` est ce que la SPA affiche (`problemMessageOf`). Un `409` au
    `detail` vide y produirait un « une erreur est survenue » inutile — d'où
    l'assertion sur le texte, pas seulement sur le code.
    """
    owner = await signed_in(crud_client, "dernier")
    base = await create_base(crud_client)

    response = await crud_client.delete(f"{KB_URL}/{base['id']}/members/{owner['id']}")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["type"] == "/problems/conflict"
    assert "propriétaire" in response.json()["detail"]


async def test_demoting_the_last_owner_answers_409(crud_client: AsyncClient) -> None:
    """Même refus pour le sélecteur de rôle, qui vise la même ligne que le retrait."""
    owner = await signed_in(crud_client, "dernier-role")
    base = await create_base(crud_client)

    response = await crud_client.put(
        f"{KB_URL}/{base['id']}/members/{owner['id']}", json={"role": "viewer"}
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert len(response.json()["detail"]) > 0


async def test_a_member_cannot_manage_the_members(crud_client: AsyncClient) -> None:
    """Gérer les membres est réservé à l'`owner` (matrice §4)."""
    await signed_in(crud_client, "proprio")
    base = await create_base(crud_client)
    editor = await register(crud_client, "editeur")
    await crud_client.put(f"{KB_URL}/{base['id']}/members/{editor['id']}", json={"role": "editor"})
    await log_in(crud_client, editor)

    response = await crud_client.put(
        f"{KB_URL}/{base['id']}/members/{editor['id']}", json={"role": "owner"}
    )

    assert response.status_code == HTTPStatus.FORBIDDEN


async def test_the_ingestion_derives_every_metadata_from_the_received_bytes(
    crud_client: AsyncClient, test_storage: LocalFilesystemStorageAdapter
) -> None:
    """ADR-014 : taille, empreinte et type de média sont calculés, jamais déclarés."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    content = file_bytes("rapport.pdf")

    document = (await ingest(crud_client, base["id"])).json()

    assert document["size_bytes"] == len(content)
    assert document["checksum_sha256"] == hashlib.sha256(content).hexdigest()
    assert document["content_type"] == "application/pdf"
    assert await test_storage.read(document["storage_uri"]) == content


async def test_an_explicit_filename_takes_precedence_over_the_upload_name(
    crud_client: AsyncClient,
) -> None:
    """Le champ `filename` du formulaire permet de renommer à l'ingestion."""
    await signed_in(crud_client)
    base = await create_base(crud_client)

    response = await crud_client.post(
        f"{KB_URL}/{base['id']}/documents/ingest",
        files={"file": ("tmp-upload.bin", file_bytes("choisi.pdf"), "application/octet-stream")},
        data={"filename": "rapport-annuel.pdf"},
    )

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json()["filename"] == "rapport-annuel.pdf"
    # Le type est déduit du nom retenu, pas du `application/octet-stream` annoncé.
    assert response.json()["content_type"] == "application/pdf"


async def test_two_different_files_produce_two_documents(crud_client: AsyncClient) -> None:
    """L'empreinte étant calculée sur les octets, deux contenus distincts se distinguent."""
    await signed_in(crud_client)
    base = await create_base(crud_client)

    first = await ingest(crud_client, base["id"], "premier.pdf")
    second = await ingest(crud_client, base["id"], "second.pdf")

    assert first.json()["id"] != second.json()["id"]
    assert (await crud_client.get(f"{KB_URL}/{base['id']}")).json()["document_count"] == 2


async def test_an_ingestion_without_a_file_is_refused(crud_client: AsyncClient) -> None:
    """La validation tient le contrat multipart : `422`, pas une erreur métier."""
    await signed_in(crud_client)
    base = await create_base(crud_client)

    response = await crud_client.post(
        f"{KB_URL}/{base['id']}/documents/ingest", data={"filename": "sans-fichier.pdf"}
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "/problems/validation-error"


async def test_an_oversized_ingestion_is_refused_with_a_413_and_leaves_no_trace(
    crud_app: FastAPI, crud_client: AsyncClient, test_storage: LocalFilesystemStorageAdapter
) -> None:
    """Audit sécurité 2026-09, constat 3 : le plafond coupe la lecture, même contrat qu'avant."""
    crud_app.dependency_overrides[get_settings] = lambda: Settings(upload_max_bytes=512)
    await signed_in(crud_client)
    base = await create_base(crud_client)

    response = await ingest(crud_client, base["id"], size=4096)

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    body = response.json()
    assert body["type"] == "/problems/payload-too-large"
    assert body["limit_bytes"] == 512
    # Taille mesurée par Starlette en écrivant la partie : refus sans relire le fichier.
    assert body["size_bytes"] == 4096
    documents = await crud_client.get(f"{KB_URL}/{base['id']}/documents")
    assert documents.json()["data"] == []
    assert not test_storage.root.exists() or not any(test_storage.root.rglob("*.pdf"))


async def test_the_ingestion_form_fields_are_bounded(crud_client: AsyncClient) -> None:
    """`filename` et `theme` sont persistés tels quels : bornés, `422` au-delà."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    url = f"{KB_URL}/{base['id']}/documents/ingest"

    for field, length in (("filename", MAX_FILENAME_LENGTH + 1), ("theme", MAX_THEME_LENGTH + 1)):
        response = await crud_client.post(url, data={field: "x" * length}, **upload())
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, field
        assert response.json()["errors"][0]["type"] == "string_too_long"


async def test_the_ingestion_is_idempotent_on_the_checksum(crud_client: AsyncClient) -> None:
    """Le rejeu d'un téléversement renvoie le document d'origine, toujours en `202`."""
    await signed_in(crud_client)
    base = await create_base(crud_client)

    first = await ingest(crud_client, base["id"])
    replay = await ingest(crud_client, base["id"])

    assert first.status_code == HTTPStatus.ACCEPTED
    assert replay.status_code == HTTPStatus.ACCEPTED
    assert replay.json()["id"] == first.json()["id"]
    documents = await crud_client.get(f"{KB_URL}/{base['id']}/documents")
    assert len(documents.json()["data"]) == 1
    assert (await crud_client.get(f"{KB_URL}/{base['id']}")).json()["document_count"] == 1


async def test_the_full_job_lifecycle(crud_client: AsyncClient) -> None:
    """Job créé implicitement à l'ingestion → suivi → annulation → seconde annulation refusée."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()
    assert document["status"] == "pending"
    job_id = document["current_job_id"]
    assert job_id is not None

    listed = await crud_client.get(f"{KB_URL}/{base['id']}/jobs")
    assert listed.status_code == HTTPStatus.OK
    assert [item["id"] for item in listed.json()["data"]] == [job_id]

    read = await crud_client.get(f"{API_PREFIX}/jobs/{job_id}")
    assert read.status_code == HTTPStatus.OK
    assert read.json()["state"] == "queued"
    assert read.json()["progress_percent"] == 0

    cancelled = await crud_client.delete(f"{API_PREFIX}/jobs/{job_id}")
    assert cancelled.status_code == HTTPStatus.NO_CONTENT
    assert (await crud_client.get(f"{API_PREFIX}/jobs/{job_id}")).json()["state"] == "cancelled"
    # Le document reflète l'annulation (migration 0005) — rien ne l'empêche
    # d'être ré-ingéré depuis cet état via un nouvel appel `.../ingest` (§3).
    after = await crud_client.get(f"{API_PREFIX}/documents/{document['id']}")
    assert after.json()["status"] == "cancelled"

    twice = await crud_client.delete(f"{API_PREFIX}/jobs/{job_id}")
    assert twice.status_code == HTTPStatus.CONFLICT
    problem = twice.json()
    assert problem["current_state"] == "cancelled"
    assert problem["allowed_states"] == []


async def test_an_unknown_job_answers_404(crud_client: AsyncClient) -> None:
    """Un identifiant inventé ne produit pas de 500."""
    await signed_in(crud_client)

    response = await crud_client.get(f"{API_PREFIX}/jobs/{uuid.uuid4()}")

    assert response.status_code == HTTPStatus.NOT_FOUND


async def test_the_precheck_decision_is_stored_and_resumes_the_pipeline(
    crud_client: AsyncClient, dispatched_tasks: DispatchedTasks
) -> None:
    """L'aiguillage se persiste **et** réveille le job : c'est le point de reprise.

    L'ordre commit ↔ publication, lui, se vérifie dans
    `tests/api/v1/test_pipeline_dispatch.py` : ici on ne regarde que le contrat.
    """
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()

    response = await crud_client.put(
        f"{API_PREFIX}/documents/{document['id']}/precheck/decision",
        json={"decision": {"optimize_for_tables": True, "heavy_ocr": True}},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["precheck_decision"] == {
        "optimize_for_tables": True,
        "heavy_ocr": True,
        "use_parent_child": False,
        "chunk_method_override": None,
    }
    assert dispatched_tasks.calls == [
        (PRECHECK_TASK, document["current_job_id"]),
        (CHUNKING_TASK, document["current_job_id"]),
    ]


async def test_ingesting_a_document_starts_the_pipeline(
    crud_client: AsyncClient, dispatched_tasks: DispatchedTasks
) -> None:
    """Sans cette publication, un document téléversé resterait `queued` pour toujours."""
    await signed_in(crud_client)
    base = await create_base(crud_client)

    document = (await ingest(crud_client, base["id"])).json()

    assert dispatched_tasks.calls == [(PRECHECK_TASK, document["current_job_id"])]


async def test_an_omitted_option_defaults_to_false(crud_client: AsyncClient) -> None:
    """Le catalogue est fermé mais ses options sont facultatives."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()

    response = await crud_client.put(
        f"{API_PREFIX}/documents/{document['id']}/precheck/decision", json={"decision": {}}
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["precheck_decision"] == {
        "optimize_for_tables": False,
        "heavy_ocr": False,
        "use_parent_child": False,
        "chunk_method_override": None,
    }


async def test_an_unknown_precheck_option_is_refused(crud_client: AsyncClient) -> None:
    """Une option silencieusement sans effet serait pire qu'une option refusée."""
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()

    response = await crud_client.put(
        f"{API_PREFIX}/documents/{document['id']}/precheck/decision",
        json={"decision": {"chunking_profile": "paper"}},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "/problems/validation-error"


async def test_the_fine_grained_template_is_refused_for_this_version(
    crud_client: AsyncClient,
) -> None:
    """Désactivée pour cette version (2026-09-06), incident réel.

    `laws` sur un PDF réel a produit un fragment de 41 264 caractères, jamais
    mesuré aux limites du reranker en aval — `CUDA out of memory` sur un lot
    de reranking le contenant. Un document déjà ingéré avec cette option
    (avant sa désactivation) reste rejouable tel quel (`workers/tasks/
    ingestion.py`) : seule la soumission d'une **nouvelle** décision est
    concernée, cf. `tests/ingestion/test_chunking_profile.py` pour le premier
    cas.
    """
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()

    response = await crud_client.put(
        f"{API_PREFIX}/documents/{document['id']}/precheck/decision",
        json={"decision": {"heavy_ocr": True, "chunk_method_override": "laws"}},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "/problems/validation-error"


async def test_a_template_outside_the_closed_catalogue_is_refused(crud_client: AsyncClient) -> None:
    """`email` est une valeur **valide pour le moteur**, et refusée ici quand même.

    Mesuré (Phase 15, Étape 2) : posé sur un PDF, ce template rend `run=done`
    en indexant les octets bruts du fichier — 2,2 % de couverture du texte
    source. Un succès apparent qui indexe du binaire est exactement ce que le
    catalogue fermé existe pour empêcher, et le refus doit tomber au contrat,
    pas au chunking.
    """
    await signed_in(crud_client)
    base = await create_base(crud_client)
    document = (await ingest(crud_client, base["id"])).json()

    response = await crud_client.put(
        f"{API_PREFIX}/documents/{document['id']}/precheck/decision",
        json={"decision": {"chunk_method_override": "email"}},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "/problems/validation-error"


async def test_a_viewer_can_read_but_cannot_ingest(crud_client: AsyncClient) -> None:
    """Matrice §4 : l'ingestion demande `owner` ou `editor`."""
    await signed_in(crud_client, "proprio")
    base = await create_base(crud_client)
    viewer = await register(crud_client, "lecteur")
    await crud_client.put(f"{KB_URL}/{base['id']}/members/{viewer['id']}", json={"role": "viewer"})
    await log_in(crud_client, viewer)

    readable = await crud_client.get(f"{KB_URL}/{base['id']}")
    refused = await ingest(crud_client, base["id"])

    assert readable.status_code == HTTPStatus.OK
    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert refused.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)


async def test_a_viewer_cannot_modify_the_base(crud_client: AsyncClient) -> None:
    """Modifier ou supprimer une base est réservé à l'`owner`."""
    await signed_in(crud_client, "proprio")
    base = await create_base(crud_client)
    viewer = await register(crud_client, "lecteur")
    await crud_client.put(f"{KB_URL}/{base['id']}/members/{viewer['id']}", json={"role": "viewer"})
    await log_in(crud_client, viewer)

    patched = await crud_client.patch(f"{KB_URL}/{base['id']}", json={"name": "Détournée"})
    deleted = await crud_client.delete(f"{KB_URL}/{base['id']}")

    assert patched.status_code == HTTPStatus.FORBIDDEN
    assert deleted.status_code == HTTPStatus.FORBIDDEN


async def test_an_anonymous_visitor_cannot_write_on_a_public_base(
    crud_client: AsyncClient,
) -> None:
    """La lecture publique n'ouvre pas l'écriture : `401`, réparable en se connectant."""
    await signed_in(crud_client)
    base = await create_base(crud_client, visibility="public")
    crud_client.cookies.clear()

    response = await ingest(crud_client, base["id"])

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "/problems/authentication-required"


async def test_a_malformed_payload_is_refused_before_the_service(
    crud_client: AsyncClient,
) -> None:
    """La validation Pydantic tient le contrat : `422`, pas une erreur métier."""
    await signed_in(crud_client)

    response = await crud_client.post(KB_URL, json={"name": "Sans modèle"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "/problems/validation-error"


async def test_listing_members_shows_the_full_directory(crud_client: AsyncClient) -> None:
    """Défaut 1 (plan interne) : `GET …/members` liste, là où seul l'accès par
    identifiant (`GET …/members/{user_id}`) existait jusqu'ici."""
    await signed_in(crud_client, "annuaire-proprio")
    base = await create_base(crud_client)
    editor = await register(crud_client, "annuaire-editeur")
    added = await crud_client.put(
        f"{KB_URL}/{base['id']}/members/{editor['id']}", json={"role": "editor"}
    )
    assert added.status_code == HTTPStatus.CREATED

    listed = await crud_client.get(f"{KB_URL}/{base['id']}/members")

    assert listed.status_code == HTTPStatus.OK
    roles = {member["user_id"]: member["role"] for member in listed.json()}
    assert roles == {base["owner_id"]: "owner", editor["id"]: "editor"}


async def test_the_member_directory_names_every_account(crud_client: AsyncClient) -> None:
    """L'annuaire porte un nom affichable — un UUID n'identifie personne à l'écran.

    L'ajout (`PUT`) est vérifié dans la même passe que la liste : le frontend
    affiche la réponse du `PUT` avant que l'annuaire ne soit rafraîchi, un nom
    manquant y produirait un vide passager.
    """
    owner = await signed_in(crud_client, "nom-proprio", display_name="Amara Baradji")
    base = await create_base(crud_client)
    editor = await register(crud_client, "nom-editeur", display_name="Camille Dupont")

    added = await crud_client.put(
        f"{KB_URL}/{base['id']}/members/{editor['id']}", json={"role": "editor"}
    )
    listed = await crud_client.get(f"{KB_URL}/{base['id']}/members")

    assert added.json()["display_name"] == "Camille Dupont"
    names = {member["user_id"]: member["display_name"] for member in listed.json()}
    assert names == {owner["id"]: "Amara Baradji", editor["id"]: "Camille Dupont"}


async def test_listing_members_is_reserved_to_the_owner(crud_client: AsyncClient) -> None:
    """Matrice §4 : composer une base est une action d'`owner`, comme `read_member`."""
    await signed_in(crud_client, "annuaire-proprio2")
    base = await create_base(crud_client)
    viewer = await register(crud_client, "annuaire-lecteur")
    await crud_client.put(f"{KB_URL}/{base['id']}/members/{viewer['id']}", json={"role": "viewer"})
    await log_in(crud_client, viewer)

    response = await crud_client.get(f"{KB_URL}/{base['id']}/members")

    assert response.status_code == HTTPStatus.FORBIDDEN
