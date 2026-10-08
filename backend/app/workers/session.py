"""Couture synchrone ↔ asynchrone des tâches Celery : une session par tâche.

Une tâche Celery est un appel **synchrone** ; tout l'accès aux données de
RAGEngine est **asynchrone** (SQLAlchemy 2 + asyncpg). `run_in_session` est le
seul point de couture : il ouvre une boucle d'événements, y déroule une session
transactionnelle, la referme, et rend la main. Aucune tâche ne recâble ça.

Deux points techniques, tous deux non négociables :

1. `asyncio.run` **est** correct ici. Un worker Celery classique (préfork,
   solo ou thread — les seuls modes utilisés par `docker/docker-compose.yml`)
   n'a aucune boucle d'événements active dans le process qui exécute la tâche :
   `asyncio.run` peut donc en créer une. Cela cesserait d'être vrai sous un
   worker `gevent`/`eventlet`, qui exigerait un pont différent.
2. Le moteur du worker utilise **`NullPool`**, contrairement à celui de la
   façade (`app.db.session`). `asyncio.run` **ferme** sa boucle en sortant : une
   connexion asyncpg rendue à un pool serait rattachée à une boucle morte et
   échouerait au prochain emprunt, c'est-à-dire dans la tâche suivante.
   `NullPool` ouvre une connexion par session et la ferme avec elle — c'est le
   prix, assumé, du modèle « une boucle d'événements par tâche ».
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_worker_engine(settings: Settings | None = None) -> AsyncEngine:
    """Construit le moteur d'un process worker : une connexion par session.

    `pool_pre_ping` n'aurait aucun sens avec `NullPool` — la connexion vient
    d'être ouverte, elle n'a pas eu le temps de mourir.
    """
    resolved = settings if settings is not None else get_settings()
    return create_async_engine(resolved.database_url, poolclass=NullPool, future=True)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Renvoie la fabrique de sessions du worker, créée au premier appel.

    Singleton paresseux : importer ce module (dans un test, une commande
    `celery inspect`…) ne doit jamais suffire à ouvrir une connexion.
    """
    global _engine, _session_factory
    if _session_factory is None:
        _engine = create_worker_engine()
        _session_factory = async_sessionmaker(bind=_engine, expire_on_commit=False, autoflush=True)
    return _session_factory


def dispose_engine() -> None:
    """Ferme le moteur du worker — branché sur le signal `worker_shutdown`.

    Synchrone par nécessité : les signaux Celery le sont, et il n'y a plus de
    boucle d'événements vivante à ce moment-là.
    """
    global _engine, _session_factory
    engine, _engine, _session_factory = _engine, None, None
    if engine is not None:
        asyncio.run(engine.dispose())


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session transactionnelle d'une tâche : commit en sortie, rollback sur exception.

    Même politique que `app.db.session.session_scope`, mais branchée sur le
    moteur `NullPool` du worker (cf. l'en-tête du module).
    """
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


def run_in_session[R](handler: Callable[[AsyncSession], Awaitable[R]]) -> R:
    """Exécute `handler` dans une session transactionnelle, depuis du code synchrone.

    Le résultat est renvoyé **après** le commit : une tâche qui publie la
    tâche suivante sur ce résultat a la garantie que l'état qu'elle vient
    d'écrire est visible par le worker qui la reprendra.

    Args:
        handler: coroutine prenant la session ouverte.

    Returns:
        La valeur produite par `handler`.
    """

    async def _run() -> R:
        async with session_scope() as db:
            return await handler(db)

    return asyncio.run(_run())
