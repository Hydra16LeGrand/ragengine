"""Application de test montant les routeurs d'identité sur la session du test.

Les routeurs livrés ici ne sont **pas** enregistrés dans `app/api/v1/router.py` :
ce fichier d'agrégation est câblé en fenêtre principale. Les tests les montent
donc eux-mêmes, exactement sous le préfixe prévu, ce qui vérifie au passage que
les chemins annoncés dans le rapport sont bien ceux que produit le routeur.

Le stockage des fichiers est redirigé vers un répertoire jetable plutôt que vers
le volume du conteneur, et reste **observable** : un test peut vérifier qu'un
octet a bien été écrit. La publication Celery, elle, est déjà interceptée pour
toute la suite par `dispatched_tasks` (`tests/conftest.py`), qui remplace la
seule résolution de tâche sans toucher au moment de la publication.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_storage
from app.api.v1.routes import (
    api_keys,
    auth,
    documents,
    evaluations,
    jobs,
    knowledge_bases,
    retrieval,
)
from app.db.session import get_db_session
from app.main import API_PREFIX
from app.providers.storage import LocalFilesystemStorageAdapter, StoragePort
from tests.conftest import BASE_URL


@pytest.fixture
def identity_app(app: FastAPI, db_session: AsyncSession) -> FastAPI:
    """Application réelle, augmentée des routeurs d'identité et de la session du test.

    La dépendance de session est remplacée par une qui **ne commit pas** : la
    transaction du test reste la seule maîtresse du cycle de vie des données.
    """

    async def _test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(api_keys.router, prefix=API_PREFIX)
    app.dependency_overrides[get_db_session] = _test_session
    return app


@pytest.fixture
async def identity_client(identity_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client HTTP conservant les cookies d'une requête à l'autre, comme un navigateur."""
    transport = ASGITransport(app=identity_app)
    async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def test_storage(tmp_path: Path) -> LocalFilesystemStorageAdapter:
    """Stockage réel, sur un répertoire jetable : l'adaptateur n'est pas simulé.

    Les tests d'API vérifient donc le vrai chemin d'écriture (ADR-014), sans
    dépendre du volume Docker monté en production.
    """
    return LocalFilesystemStorageAdapter(root=tmp_path / "documents")


@pytest.fixture
def crud_app(identity_app: FastAPI, test_storage: LocalFilesystemStorageAdapter) -> FastAPI:
    """Application d'identité augmentée des routeurs CRUD, eux non plus non enregistrés.

    Elle est construite **au-dessus** d'`identity_app` plutôt qu'à côté : les
    parcours CRUD ont besoin de s'authentifier par de vraies requêtes HTTP
    (`POST /users`, `/sessions`), et la substitution de la
    session de test y est déjà posée.
    """

    def _test_storage() -> StoragePort:
        return test_storage

    identity_app.include_router(knowledge_bases.router, prefix=API_PREFIX)
    identity_app.include_router(documents.router, prefix=API_PREFIX)
    identity_app.include_router(jobs.router, prefix=API_PREFIX)
    identity_app.dependency_overrides[get_storage] = _test_storage
    return identity_app


@pytest.fixture
async def crud_client(crud_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client HTTP sur l'application complète (identité + CRUD)."""
    transport = ASGITransport(app=crud_app)
    async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def retrieval_app(crud_app: FastAPI) -> FastAPI:
    """Application CRUD augmentée du routeur de retrieval, lui aussi non enregistré.

    Montée **au-dessus** de `crud_app` plutôt qu'à côté : la matrice de
    permissions du retrieval s'appuie sur les mêmes comptes, membres et bases
    que les parcours CRUD, déjà authentifiables sur cette application.
    """
    crud_app.include_router(retrieval.router, prefix=API_PREFIX)
    return crud_app


@pytest.fixture
async def retrieval_client(retrieval_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client HTTP sur l'application complète (identité + CRUD + retrieval)."""
    transport = ASGITransport(app=retrieval_app)
    async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def evaluation_app(crud_app: FastAPI) -> FastAPI:
    """Application CRUD augmentée du routeur d'évaluation, lui aussi non enregistré.

    Montée sur `crud_app` (pas `retrieval_app`) : les runs d'évaluation ne
    dépendent d'aucune route de retrieval, seulement de comptes/bases déjà
    authentifiables sur l'application CRUD.
    """
    crud_app.include_router(evaluations.router, prefix=API_PREFIX)
    return crud_app


@pytest.fixture
async def evaluation_client(evaluation_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client HTTP sur l'application complète (identité + CRUD + évaluation)."""
    transport = ASGITransport(app=evaluation_app)
    async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client
