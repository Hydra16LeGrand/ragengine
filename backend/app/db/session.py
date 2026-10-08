"""Moteur asynchrone, fabrique de sessions et dépendance FastAPI.

Le moteur est un **singleton paresseux** : il n'est jamais construit à l'import
du module, sinon importer `app.db.session` (dans un test, une tâche Celery ou
une commande Alembic) ouvrirait un pool vers PostgreSQL sans qu'on l'ait
demandé, et figerait la configuration avant même que `Settings` soit résolue.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Construit un moteur asynchrone neuf (utile aux tests et aux scripts)."""
    resolved = settings if settings is not None else get_settings()
    return create_async_engine(
        resolved.database_url,
        # Une connexion morte (redémarrage de PostgreSQL, coupure réseau) est
        # détectée à l'emprunt plutôt qu'au milieu d'une requête métier.
        pool_pre_ping=True,
        future=True,
    )


def get_engine() -> AsyncEngine:
    """Renvoie le moteur du process, créé au premier appel."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Renvoie la fabrique de sessions du process, créée au premier appel.

    `expire_on_commit=False` : après le commit de fin de requête, les objets
    restent lisibles pour la sérialisation de la réponse, sans un `SELECT`
    supplémentaire par attribut.
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=True,
        )
    return _session_factory


async def dispose_engine() -> None:
    """Ferme le pool de connexions et réinitialise le singleton.

    À appeler à l'arrêt de l'application (`lifespan`), du worker Celery ou entre
    deux tests qui changent de base.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Dépendance FastAPI : une session par requête, transaction bornée.

    Sortie normale du handler ⇒ `COMMIT` ; exception (y compris une
    `RAGEngineError` traduite en Problem Details) ⇒ `ROLLBACK` puis propagation.

    **`scope="function"` sur `DbSession` est un invariant, pas un détail.** Par
    défaut (`scope="request"`), FastAPI referme une dépendance `yield` **après**
    l'envoi de la réponse — vérifié à la source (`fastapi/routing.py::
    request_response`) et empiriquement (reproduction minimale : une tâche de
    fond s'exécute avant la fermeture de la dépendance). Sans `scope="function"`,
    ce `COMMIT` n'a donc **pas encore eu lieu** quand le client reçoit sa
    réponse : un second client qui enchaîne aussitôt une requête dépendante
    (register → login, ou tout `POST` suivi d'un `GET`) peut lire l'état
    précédent sous `READ COMMITTED` — silencieusement, sans erreur. Constaté en
    conditions réelles (E2E Phase 4, contre la stack déployée, pas en test
    unitaire) : ~30 % des enchaînements `POST /sessions` juste après
    `POST /users` renvoyaient `401`. `scope="function"` referme la dépendance
    **avant** l'envoi de la réponse : le commit est garanti visible dès que le
    client reçoit un octet.
    """
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


DbSession = Annotated[AsyncSession, Depends(get_db_session, scope="function")]
"""Alias d'injection à utiliser dans les routers : `session: DbSession`."""


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session transactionnelle hors requête HTTP (tâches Celery, scripts).

    Même politique que `get_db_session` : commit en sortie normale, rollback sur
    exception.
    """
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
