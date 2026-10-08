"""Parcours HTTP complets des organisations (ADR-022).

Ces tests décrivent le contrat tel que le frontend le consommera — codes de
statut, `type` des Problem Details, et la matrice ADR-022 appliquée bout en
bout : un non-membre qui ne voit pas une organisation (`404`, jamais `403` ni
`401`), un invité qui ne peut pas en créer une supplémentaire, le dernier
`owner` protégé, la résolution d'organisation par défaut à la création d'une
base de connaissances.

Le routeur `organizations` est déjà enregistré par `app.api.v1.router`
(inclus dans `create_app()`) : contrairement aux autres suites de ce paquet,
qui re-montent leurs routeurs pour aussi documenter le préfixe attendu, il
suffit ici de la fixture `crud_client` existante (identité + KB), qui
substitue déjà la session de test sur l'application complète."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from httpx import AsyncClient

from app.core.errors import PROBLEM_MEDIA_TYPE
from app.main import API_PREFIX
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


ORG_URL = f"{API_PREFIX}/organizations"


KB_URL = f"{API_PREFIX}/knowledge-bases"


async def register(client: AsyncClient, prefix: str = "orgapi") -> dict[str, Any]:
    """Inscrit un compte réel et renvoie sa représentation (identifiant compris)."""
    payload = {
        "email": factories.unique_email(prefix),
        "password": PASSWORD,
        "display_name": "Amara",
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


async def signed_in(client: AsyncClient, prefix: str = "orgapi") -> dict[str, Any]:
    """Inscrit un compte et le connecte immédiatement."""
    user = await register(client, prefix)
    await log_in(client, user)
    return user


async def create_org(client: AsyncClient, **overrides: object) -> dict[str, Any]:
    """Crée une organisation par HTTP et renvoie sa représentation."""
    payload: dict[str, object] = {"name": "Baradji Services"} | overrides
    response = await client.post(ORG_URL, json=payload)
    assert response.status_code == HTTPStatus.CREATED, response.text
    return dict(response.json())


async def personal_org_id(client: AsyncClient) -> str:
    """Identifiant de l'organisation personnelle du principal courant.

    Fraîchement inscrit, un compte n'en a qu'une : la première (et seule)
    page de `GET /organizations` la contient.
    """
    listed = await client.get(ORG_URL)
    assert listed.status_code == HTTPStatus.OK, listed.text
    data = listed.json()["data"]
    assert len(data) == 1
    return str(data[0]["id"])


async def test_the_full_organization_lifecycle(crud_client: AsyncClient) -> None:
    """Création → lecture → mise à jour → liste → suppression."""
    await signed_in(crud_client)
    created = await create_org(crud_client, name="Équipe Qualité")
    assert created["slug"] == "equipe-qualite"
    assert created["principal_role"] == "owner"

    read = await crud_client.get(f"{ORG_URL}/{created['id']}")
    assert read.status_code == HTTPStatus.OK
    assert read.json()["principal_role"] == "owner"

    updated = await crud_client.patch(f"{ORG_URL}/{created['id']}", json={"name": "Qualité 2026"})
    assert updated.status_code == HTTPStatus.OK
    assert updated.json()["name"] == "Qualité 2026"
    assert updated.json()["slug"] == "equipe-qualite"

    listed = await crud_client.get(ORG_URL)
    assert listed.status_code == HTTPStatus.OK
    assert created["id"] in {item["id"] for item in listed.json()["data"]}

    deleted = await crud_client.delete(f"{ORG_URL}/{created['id']}")
    assert deleted.status_code == HTTPStatus.NO_CONTENT
    assert (await crud_client.get(f"{ORG_URL}/{created['id']}")).status_code == HTTPStatus.NOT_FOUND


async def test_registering_provisions_a_personal_organization(crud_client: AsyncClient) -> None:
    """ADR-022 : un compte fraîchement inscrit possède déjà une organisation."""
    await signed_in(crud_client)

    listed = await crud_client.get(ORG_URL)

    assert listed.status_code == HTTPStatus.OK
    assert len(listed.json()["data"]) == 1
    assert listed.json()["data"][0]["principal_role"] == "owner"


async def test_creating_an_organization_requires_authentication(
    crud_client: AsyncClient,
) -> None:
    response = await crud_client.post(ORG_URL, json={"name": "Essai"})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "/problems/authentication-required"


async def test_creating_a_duplicate_slug_answers_409(crud_client: AsyncClient) -> None:
    """L'unicité **globale** du slug, à la différence d'une KB par propriétaire."""
    await signed_in(crud_client, "dup1")
    await create_org(crud_client, slug="partage")
    other = await register(crud_client, "dup2")
    await log_in(crud_client, other)

    response = await crud_client.post(ORG_URL, json={"name": "Autre", "slug": "partage"})

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["type"] == "/problems/conflict"


async def test_a_stranger_gets_404_not_403(crud_client: AsyncClient) -> None:
    await signed_in(crud_client, "org-owner")
    created = await create_org(crud_client)
    stranger = await register(crud_client, "org-stranger")
    await log_in(crud_client, stranger)

    response = await crud_client.get(f"{ORG_URL}/{created['id']}")

    assert response.status_code == HTTPStatus.NOT_FOUND


async def test_the_member_lifecycle(crud_client: AsyncClient) -> None:
    """Ajout (`201`) → changement de rôle (`200`) → lecture → retrait (`204`)."""
    await signed_in(crud_client, "orgproprio")
    org = await create_org(crud_client)
    other = await register(crud_client, "orgmembre")
    member_url = f"{ORG_URL}/{org['id']}/members/{other['id']}"

    added = await crud_client.put(member_url, json={"role": "member"})
    assert added.status_code == HTTPStatus.CREATED
    assert added.json()["role"] == "member"

    promoted = await crud_client.put(member_url, json={"role": "owner"})
    assert promoted.status_code == HTTPStatus.OK
    assert promoted.json()["role"] == "owner"

    read = await crud_client.get(member_url)
    assert read.status_code == HTTPStatus.OK
    assert read.json()["user_id"] == other["id"]

    removed = await crud_client.delete(member_url)
    assert removed.status_code == HTTPStatus.NO_CONTENT
    assert (await crud_client.get(member_url)).status_code == HTTPStatus.NOT_FOUND


async def test_removing_the_last_owner_answers_409(crud_client: AsyncClient) -> None:
    owner = await signed_in(crud_client, "orgdernier")
    org = await create_org(crud_client)

    response = await crud_client.delete(f"{ORG_URL}/{org['id']}/members/{owner['id']}")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["type"] == "/problems/conflict"


async def test_a_member_cannot_manage_the_members(crud_client: AsyncClient) -> None:
    """Mutation réservée à l'`owner` (à la différence de la lecture, ouverte à tout membre)."""
    await signed_in(crud_client, "orgproprio2")
    org = await create_org(crud_client)
    member = await register(crud_client, "orgmembre2")
    await crud_client.put(f"{ORG_URL}/{org['id']}/members/{member['id']}", json={"role": "member"})
    await log_in(crud_client, member)

    response = await crud_client.put(
        f"{ORG_URL}/{org['id']}/members/{member['id']}", json={"role": "owner"}
    )

    assert response.status_code == HTTPStatus.FORBIDDEN


async def test_any_member_can_read_the_member_directory(crud_client: AsyncClient) -> None:
    """Différence assumée avec une KB : l'annuaire est ouvert à tout membre."""
    await signed_in(crud_client, "orgproprio3")
    org = await create_org(crud_client)
    member = await register(crud_client, "orgmembre3")
    await crud_client.put(f"{ORG_URL}/{org['id']}/members/{member['id']}", json={"role": "member"})
    await log_in(crud_client, member)

    response = await crud_client.get(f"{ORG_URL}/{org['id']}/members")

    assert response.status_code == HTTPStatus.OK
    roles = {row["user_id"]: row["role"] for row in response.json()}
    assert roles[member["id"]] == "member"


async def test_deleting_an_organization_with_a_knowledge_base_is_refused(
    crud_client: AsyncClient,
) -> None:
    await signed_in(crud_client, "orgavecbase")
    org = await create_org(crud_client)
    kb_response = await crud_client.post(
        KB_URL,
        json={
            "name": "Base rattachée",
            "embedding_model_id": "bge-m3",
            "organization_id": org["id"],
        },
    )
    assert kb_response.status_code == HTTPStatus.CREATED, kb_response.text

    response = await crud_client.delete(f"{ORG_URL}/{org['id']}")

    assert response.status_code == HTTPStatus.CONFLICT


async def test_creating_a_kb_defaults_to_the_single_organization(
    crud_client: AsyncClient,
) -> None:
    """Un compte fraîchement inscrit n'a qu'une organisation : la KB s'y résout
    sans que le client n'ait à préciser `organization_id`."""
    await signed_in(crud_client, "kbdefaut")
    expected_org_id = await personal_org_id(crud_client)

    response = await crud_client.post(
        KB_URL, json={"name": "Base par défaut", "embedding_model_id": "bge-m3"}
    )

    assert response.status_code == HTTPStatus.CREATED, response.text
    assert response.json()["organization_id"] == expected_org_id


async def test_creating_a_kb_with_an_explicit_organization_id_succeeds_for_a_member(
    crud_client: AsyncClient,
) -> None:
    await signed_in(crud_client, "kbexplicite")
    org = await create_org(crud_client, name="Seconde organisation")

    response = await crud_client.post(
        KB_URL,
        json={
            "name": "Base explicite",
            "embedding_model_id": "bge-m3",
            "organization_id": org["id"],
        },
    )

    assert response.status_code == HTTPStatus.CREATED, response.text
    assert response.json()["organization_id"] == org["id"]


async def test_creating_a_kb_with_an_organization_id_the_principal_does_not_belong_to_answers_404(
    crud_client: AsyncClient,
) -> None:
    """Même rationale de confidentialité qu'une base privée : `404`, pas `403`."""
    await signed_in(crud_client, "kbautrui")
    foreign_org = await create_org(crud_client, name="Pas la mienne")
    stranger = await register(crud_client, "kbetranger")
    await log_in(crud_client, stranger)

    response = await crud_client.post(
        KB_URL,
        json={
            "name": "Base intruse",
            "embedding_model_id": "bge-m3",
            "organization_id": foreign_org["id"],
        },
    )

    assert response.status_code == HTTPStatus.NOT_FOUND


async def test_creating_a_kb_with_several_organizations_requires_an_explicit_id(
    crud_client: AsyncClient,
) -> None:
    """Plusieurs organisations, `organization_id` omis : erreur explicite, pas
    un choix arbitraire et silencieux."""
    await signed_in(crud_client, "kbplusieurs")
    await create_org(crud_client, name="Deuxième organisation")

    response = await crud_client.post(
        KB_URL, json={"name": "Base ambiguë", "embedding_model_id": "bge-m3"}
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "/problems/validation-error"
