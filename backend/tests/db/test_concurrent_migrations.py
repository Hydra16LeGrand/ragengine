"""Plusieurs `alembic upgrade head` simultanés sur une base vierge (ADR-072).

`api`, `worker-default`, `worker-beat` et `worker-gpu` lancent chacun les migrations au
démarrage (`docker-entrypoint.sh`) : sur une base vide, ou lors d'un déploiement qui apporte
une migration, ils entrent en course. Sans verrou, le perdant échoue (`relation ... already
exists`, type ou ligne de version dupliqués) et son conteneur redémarre en boucle.

Ces tests jouent la course pour de bon, contre le PostgreSQL de test : N **processus**
(`alembic upgrade head`, comme dans les conteneurs), lâchés ensemble. Des threads ne
conviendraient pas : `logging.config.fileConfig` d'Alembic n'y est pas sûre.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.postgres import (
    ALEMBIC_INI,
    BACKEND_DIR,
    drop_database,
    recreate_database,
    resolve_test_database_url,
    run_downgrade,
    run_upgrade,
)

CONCURRENT_UPGRADES = 4
ROUNDS = 3
PROCESS_TIMEOUT_SECONDS = 120


@pytest.fixture
def virgin_database(database_url: URL) -> Iterator[URL]:
    """Base jetable, propre à ce module."""
    url = resolve_test_database_url(f"{database_url.database}_concurrent")
    recreate_database(url)
    try:
        yield url
    finally:
        drop_database(url)


def _race(url: URL) -> list[str]:
    """Lance `CONCURRENT_UPGRADES` `alembic upgrade head` en même temps ; renvoie les échecs.

    Chaque processus lit sa base dans l'environnement (`POSTGRES_*`), exactement comme le
    point d'entrée du conteneur.
    """
    environment = {
        **os.environ,
        "POSTGRES_HOST": url.host or "127.0.0.1",
        "POSTGRES_PORT": str(url.port),
        "POSTGRES_DB": url.database or "",
        "POSTGRES_USER": url.username or "",
        "POSTGRES_PASSWORD": url.password or "",
    }
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=BACKEND_DIR,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(CONCURRENT_UPGRADES)
    ]
    failures: list[str] = []
    for process in processes:
        output, _ = process.communicate(timeout=PROCESS_TIMEOUT_SECONDS)
        if process.returncode != 0:
            causes = [
                line for line in output.splitlines() if "Error" in line or "Exception" in line
            ]
            failures.append(causes[-1].strip() if causes else (output.strip() or "sans sortie"))
    return failures


async def _version_rows(url: URL) -> int:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return int(
                (
                    await connection.execute(text("SELECT count(*) FROM alembic_version"))
                ).scalar_one()
            )
    finally:
        await engine.dispose()


def test_concurrent_upgrades_on_an_empty_database_all_succeed(virgin_database: URL) -> None:
    errors = _race(virgin_database)

    assert not errors, f"{len(errors)} upgrade(s) concurrent(s) en échec : {errors[0]}"
    assert asyncio.run(_version_rows(virgin_database)) == 1


def test_concurrent_upgrades_stay_correct_over_several_rounds(database_url: URL) -> None:
    """Plusieurs courses d'affilée, chacune sur une base vierge : pas un coup de chance."""
    for round_number in range(ROUNDS):
        url = resolve_test_database_url(f"{database_url.database}_concurrent_{round_number}")
        recreate_database(url)
        try:
            errors = _race(url)
            assert not errors, f"manche {round_number} : {errors[0]}"
        finally:
            drop_database(url)


def test_concurrent_upgrades_apply_a_pending_migration_once(virgin_database: URL) -> None:
    """Déploiement qui apporte une migration : base existante, une révision en retard."""
    run_upgrade(virgin_database)
    run_downgrade(virgin_database, "-1")

    errors = _race(virgin_database)

    assert not errors, f"{len(errors)} upgrade(s) concurrent(s) en échec : {errors[0]}"
    assert asyncio.run(_version_rows(virgin_database)) == 1
