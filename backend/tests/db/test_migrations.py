"""Migrations Alembic : rejouables de bout en bout sur une base vierge."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import URL, Connection, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.types import ENUM_TYPE_NAMES
from tests.postgres import (
    drop_database,
    recreate_database,
    resolve_test_database_url,
    run_downgrade,
    run_upgrade,
)

EXPECTED_TABLES = frozenset(Base.metadata.tables)

TABLES_QUERY = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
ENUMS_QUERY = "SELECT typname FROM pg_type WHERE typtype = 'e'"


async def _fetch_names(url: URL, query: str) -> set[str]:
    """Liste les objets du schéma public, sans passer par l'ORM."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return {row[0] for row in (await connection.execute(text(query))).all()}
    finally:
        await engine.dispose()


def names(url: URL, query: str) -> set[str]:
    """Variante synchrone, appelable depuis un test non asynchrone."""
    return asyncio.run(_fetch_names(url, query))


@pytest.fixture
def virgin_database(database_url: URL) -> Iterator[URL]:
    """Base jetable, distincte de celle des autres tests, pour jouer le cycle complet."""
    url = resolve_test_database_url(f"{database_url.database}_migrations")
    recreate_database(url)
    try:
        yield url
    finally:
        drop_database(url)


def test_upgrade_head_then_downgrade_base_is_replayable(virgin_database: URL) -> None:
    """`upgrade head` → `downgrade base` → `upgrade head` sans résidu ni collision.

    Le second `upgrade` est la vraie assurance : si le `downgrade` oubliait de
    supprimer les types `ENUM`, il échouerait sur « type already exists ».
    """
    run_upgrade(virgin_database)

    tables = names(virgin_database, TABLES_QUERY)
    assert tables >= EXPECTED_TABLES, "toutes les tables du modèle doivent être créées"
    assert "alembic_version" in tables
    assert set(ENUM_TYPE_NAMES) <= names(virgin_database, ENUMS_QUERY)

    run_downgrade(virgin_database)

    assert names(virgin_database, TABLES_QUERY) == {"alembic_version"}
    assert names(virgin_database, ENUMS_QUERY) == set()

    run_upgrade(virgin_database)

    assert names(virgin_database, TABLES_QUERY) >= EXPECTED_TABLES


def test_migrated_schema_matches_the_models(migrated_database: URL) -> None:
    """Aucune dérive entre les modèles et la migration (équivalent d'`alembic check`)."""

    def _diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        return list(compare_metadata(context, Base.metadata))

    async def _run() -> list[object]:
        engine = create_async_engine(migrated_database, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(_diff)
        finally:
            await engine.dispose()

    assert asyncio.run(_run()) == []
