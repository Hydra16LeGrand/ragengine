"""Fixtures d'intégration PostgreSQL, partagées par toute la suite.

Elles sont **définies ici et importées par `tests/conftest.py`** plutôt que
déclarées dans un `conftest.py` de sous-répertoire : une fixture de portée
session déclarée deux fois est instanciée deux fois, ce qui recréerait la base
au milieu de la session. Un seul point de définition garantit une seule base
migrée pour tous les paquets de tests (`tests/db`, `tests/services`, `tests/api`).

Le modèle repose sur `JSONB`, `TEXT[]`, des types `ENUM` natifs et un index
fonctionnel : SQLite ne sait rien en faire, ces tests s'exécutent donc contre un
vrai PostgreSQL. Si aucun serveur n'est joignable, ils sont **skippés** — les
tests purs (machine à états, curseur, schémas, hachage) restent verts sans base.

URL cible, par ordre de priorité :

1. `TEST_DATABASE_URL` ;
2. à défaut, les identifiants de `Settings` appliqués au port publié sur
   l'hôte par `docker/docker-compose.yml` (`RAGENGINE_POSTGRES_HOST_PORT`).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from urllib.parse import quote

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

# Surchargeable pour faire tourner plusieurs sessions pytest en parallèle sans
# qu'elles se détruisent mutuellement leur base (`DROP DATABASE ... WITH (FORCE)`
# en début de session) : chaque session prend un nom distinct. Les bases dérivées
# (`_migrations`, `_dispatch`) suivent, elles préfixent ce nom.
TEST_DATABASE_NAME = os.environ.get("RAGENGINE_TEST_DATABASE_NAME", "ragengine_test")
MAINTENANCE_DATABASE = "postgres"

# Lus à l'import du module, donc avant que la fixture d'isolation
# d'environnement des tests unitaires ne les efface.
_HOST_PORT = os.environ.get("RAGENGINE_POSTGRES_HOST_PORT", "5433")
_EXPLICIT_URL = os.environ.get("TEST_DATABASE_URL", "")


def resolve_test_database_url(database: str = TEST_DATABASE_NAME) -> URL:
    """Construit l'URL de la base de test, sans jamais coder un secret en dur."""
    if _EXPLICIT_URL:
        return make_url(_EXPLICIT_URL).set(database=database)
    settings = Settings()
    user = quote(settings.postgres_user, safe="")
    password = quote(settings.postgres_password.get_secret_value(), safe="")
    return make_url(f"postgresql+asyncpg://{user}:{password}@127.0.0.1:{_HOST_PORT}/{database}")


async def _execute_autocommit(url: URL, statements: Sequence[str]) -> None:
    """Exécute des instructions hors transaction (`CREATE`/`DROP DATABASE`)."""
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            for statement in statements:
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


def recreate_database(url: URL) -> None:
    """Détruit puis recrée la base cible, pour partir d'un schéma vierge."""
    maintenance = url.set(database=MAINTENANCE_DATABASE)
    name = url.database
    asyncio.run(
        _execute_autocommit(
            maintenance,
            [f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', f'CREATE DATABASE "{name}"'],
        )
    )


def drop_database(url: URL) -> None:
    """Supprime la base cible, connexions résiduelles comprises."""
    maintenance = url.set(database=MAINTENANCE_DATABASE)
    asyncio.run(
        _execute_autocommit(maintenance, [f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'])
    )


def alembic_config(url: URL) -> Config:
    """Configuration Alembic pointant sur la base fournie.

    L'URL passe par `config.attributes` : `set_main_option` la ferait traverser
    configparser, dont l'interpolation `%` casserait un mot de passe encodé.
    """
    config = Config(str(ALEMBIC_INI))
    config.attributes["sqlalchemy_url"] = url.render_as_string(hide_password=False)
    return config


def run_upgrade(url: URL, revision: str = "head") -> None:
    """Applique les migrations jusqu'à `revision`."""
    command.upgrade(alembic_config(url), revision)


def run_downgrade(url: URL, revision: str = "base") -> None:
    """Défait les migrations jusqu'à `revision`."""
    command.downgrade(alembic_config(url), revision)


def _server_is_reachable(url: URL) -> bool:
    """Teste l'accès au serveur sans faire échouer la session de tests."""
    try:
        asyncio.run(_execute_autocommit(url.set(database=MAINTENANCE_DATABASE), ["SELECT 1"]))
    except (SQLAlchemyError, OSError):
        return False
    return True


@pytest.fixture(scope="session")
def database_url() -> URL:
    """URL de la base de test, ou `skip` si PostgreSQL est injoignable."""
    url = resolve_test_database_url()
    if not _server_is_reachable(url):
        pytest.skip(
            "PostgreSQL injoignable : définir TEST_DATABASE_URL ou démarrer la stack Docker."
        )
    return url


@pytest.fixture(scope="session")
def migrated_database(database_url: URL) -> Iterator[URL]:
    """Base dédiée, créée et migrée pour la session, détruite à la fin."""
    recreate_database(database_url)
    run_upgrade(database_url)
    try:
        yield database_url
    finally:
        drop_database(database_url)


@pytest.fixture
async def engine(migrated_database: URL) -> AsyncIterator[AsyncEngine]:
    """Moteur par test : une connexion asyncpg appartient à sa boucle d'événements."""
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session isolée : tout est annulé en fin de test, la base ne dérive pas.

    La session s'attache à une transaction externe et travaille en points de
    sauvegarde : un `commit()` du code testé reste visible dans le test, puis
    disparaît au `rollback` final.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
