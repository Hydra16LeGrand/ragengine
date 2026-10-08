"""Moteur factice à état partagé entre processus, adossé à Redis (ADR-071).

`FakeIngestionEngine` garde son état dans le process : une API et un worker
Celery lancés dans deux conteneurs ne se voient pas, et l'ingestion casse (le
document téléversé par l'API est introuvable pour le worker). Cette sous-classe
déplace cet état dans **une clé Redis** : chaque opération du port prend un
verrou, recharge l'état, s'exécute, puis le réécrit. Aucune règle du moteur
factice n'est redéfinie — seul le support de l'état change.

Pourquoi Redis, et pas l'exécution des tâches dans le process de l'API (Celery
« eager ») : exécuter une tâche au sein d'une requête asynchrone y appelle
`asyncio.run` dans une boucle déjà active (`app.workers.session`), et le mode
eager ignore les `countdown` des sondages. Redis est de toute façon déjà
démarré (broker Celery) : le chemin d'essai garde la même topologie qu'en
production (API et worker dans deux conteneurs), le code de production n'est pas
touché, et rien n'y est exécuté autrement que par un vrai worker.

Mêmes limites que le moteur factice : aucune recherche sémantique, aucun OCR,
aucun reranking. Et une de plus : l'état n'est pas versionné — vider Redis
l'efface, ce qui est voulu pour une démonstration. Refusé en staging et en
production, comme `fake` (`Settings`, ADR-070).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final

import redis.asyncio as redis

from app.providers.engine.fake import FakeIngestionEngine

SHARED_FAKE_ENGINE_ID: Final = "fake-shared"
"""Valeur de `INGESTION_ENGINE` qui sélectionne ce moteur."""

STATE_KEY: Final = "ragengine:fake-engine:state"
LOCK_KEY: Final = "ragengine:fake-engine:lock"
LOCK_TIMEOUT_SECONDS: Final = 30.0
"""Durée de vie du verrou si un process meurt en le tenant, et attente maximale pour l'obtenir."""

DEMO_POLLS_BEFORE_DONE: Final = 2
"""Sondages `running` avant `done` : assez pour que l'interface montre la progression."""


class SharedStateFakeEngine(FakeIngestionEngine):
    """`FakeIngestionEngine` dont l'état vit dans Redis, partagé par tous les process.

    Args:
        redis_url: base Redis qui porte l'état (celle de `REDIS_URL`).
        state_key / lock_key: clés de l'état et de son verrou — distinctes d'une
            instance à l'autre pour isoler deux démonstrations ou deux tests.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        state_key: str = STATE_KEY,
        lock_key: str = LOCK_KEY,
        polls_before_done: int = DEMO_POLLS_BEFORE_DONE,
    ) -> None:
        super().__init__(polls_before_done=polls_before_done)
        self._redis_url = redis_url
        self._state_key = state_key
        self._lock_key = lock_key

    def _operation(self) -> AbstractAsyncContextManager[None]:
        return self._synchronised()

    @asynccontextmanager
    async def _synchronised(self) -> AsyncIterator[None]:
        # Un client par opération : une tâche Celery ouvre sa propre boucle
        # d'événements (`app.workers.session`), un client gardé d'une boucle à
        # l'autre échouerait sur une boucle morte.
        # `redis-py` ne type pas `from_url` (cf. `app.core.rate_limit`).
        client = redis.from_url(self._redis_url)  # type: ignore[no-untyped-call]
        try:
            async with client.lock(
                self._lock_key,
                timeout=LOCK_TIMEOUT_SECONDS,
                blocking_timeout=LOCK_TIMEOUT_SECONDS,
            ):
                stored = await client.get(self._state_key)
                if stored is not None:
                    self.import_state(stored)
                try:
                    yield
                finally:
                    await client.set(self._state_key, self.export_state())
        finally:
            await client.aclose()
