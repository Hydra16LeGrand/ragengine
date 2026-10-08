"""Environnement Alembic — asynchrone, configuré par `Settings`.

L'URL n'est jamais écrite dans `alembic.ini` : elle vient de l'environnement via
`app.core.config.Settings`, si bien qu'une migration ne peut pas être appliquée
par erreur sur la mauvaise base parce qu'un DSN traînait dans un fichier
versionné. Un appelant programmatique (les tests) peut la surcharger en posant
`sqlalchemy.url` sur l'objet `Config`.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

# L'import du paquet enregistre toutes les tables dans `Base.metadata` : sans
# lui, un autogenerate proposerait de supprimer le schéma entier.
from app.db import models  # noqa: F401
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False` : sans lui, appliquer une migration
    # depuis un process déjà démarré (tests, script de démarrage) éteindrait
    # silencieusement tous les loggers applicatifs déjà créés.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


URL_ATTRIBUTE = "sqlalchemy_url"
"""Clé de `config.attributes` par laquelle un appelant programmatique impose l'URL."""


def get_database_url() -> str:
    """URL de la base cible.

    Ordre de résolution : surcharge programmatique (`config.attributes`, utilisée
    par les tests, à l'abri de l'interpolation `%` de configparser), puis
    `sqlalchemy.url` s'il a été posé, puis la configuration applicative.
    """
    override = config.attributes.get(URL_ATTRIBUTE)
    if isinstance(override, str) and override:
        return override
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    """Facteurs communs aux modes en ligne et hors ligne."""
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        # Sans ces deux comparaisons, un changement de type ou de défaut serveur
        # passerait inaperçu à l'autogenerate et créerait une dérive silencieuse.
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        render_as_batch=False,
    )


def run_migrations_offline() -> None:
    """Génère le SQL sans connexion (`alembic upgrade head --sql`)."""
    _configure(url=get_database_url())
    with context.begin_transaction():
        context.run_migrations()


MIGRATION_LOCK_ID = 0x52414745_4D494752
"""Clé du verrou consultatif PostgreSQL qui sérialise les migrations (ADR-072).

Les ASCII `RAGEMIGR` : une valeur fixe et propre à RAGEngine, pour ne pas entrer en collision
avec le verrou consultatif d'une autre application partageant le serveur."""


def _run_migrations(connection: Connection) -> None:
    """Exécute les migrations sur une connexion synchrone (via `run_sync`).

    `api`, `worker-default`, `worker-beat` et `worker-gpu` lancent chacun `alembic upgrade
    head` au démarrage : sans garde, ils se disputent la même base (type ou table créés deux
    fois). Le verrou est pris **dans** la transaction des migrations, avant toute lecture de la
    version courante : le processus qui arrive second attend la fin du premier, relit alors
    la version, la trouve à `head` et ne fait rien. `pg_advisory_xact_lock` est relâché au
    commit ou au rollback, jamais oublié par un processus tué.
    """
    _configure(connection=connection)
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_ID}
            )
        context.run_migrations()


async def run_migrations_async() -> None:
    """Ouvre un moteur asynchrone jetable et y applique les migrations."""
    engine = create_async_engine(get_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """Point d'entrée en ligne — le driver `asyncpg` impose une boucle d'événements."""
    asyncio.run(run_migrations_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
