"""Fixtures partagées : environnement neutralisé, application et client HTTP.

Les fixtures PostgreSQL (`database_url`, `migrated_database`, `engine`,
`db_session`) sont définies dans `tests.postgres` et réexportées ici : c'est le
seul moyen qu'une base migrée de portée session soit partagée par tous les
paquets de tests au lieu d'être recréée pour chacun.

`dispatched_tasks` est `autouse` : elle empêche toute la suite de publier sur un
vrai broker, sans jamais court-circuiter la logique qui décide du moment de la
publication.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit as rate_limit_module
from app.core.config import Settings, get_settings
from app.core.plugins import plugins_overridden
from app.core.rate_limit import close_rate_limiter
from app.db.session import get_db_session
from app.main import create_app
from app.services import documents as documents_service
from app.services import evaluations as evaluations_service
from app.services import jobs as jobs_service
from tests.in_memory_rate_limiter import InMemoryRateLimiter
from tests.postgres import (
    database_url,  # noqa: F401 - fixture réexportée
    db_session,  # noqa: F401 - fixture réexportée
    engine,  # noqa: F401 - fixture réexportée
    migrated_database,  # noqa: F401 - fixture réexportée
)

BASE_URL = "http://testserver"

# Lu à l'import du module, donc avant que `_isolated_environment` n'efface
# `REDIS_URL` — même pattern que `RAGENGINE_POSTGRES_HOST_PORT`
# (`tests/postgres.py`). Sans ce réaiguillage, `Settings.redis_url` retombe sur
# son défaut `redis://redis:6379/0` : ce nom de service n'est résolvable que
# depuis l'intérieur du réseau Docker, et l'échec de résolution DNS coûte
# plusieurs secondes **par tentative** depuis l'hôte — largement de quoi
# transformer un test HTTP de quelques requêtes en un blocage de plusieurs
# minutes, malgré l'« échec ouvert » de `app.core.rate_limit` (conçu pour un
# Redis *rapidement* injoignable — connexion refusée —, pas un nom qui ne
# résout pas du tout). `RAGENGINE_REDIS_HOST_PORT` est déjà documenté dans
# `.env.example` pour cet usage précis, mais rien ne le consommait ici.
_REDIS_HOST_PORT = os.environ.get("RAGENGINE_REDIS_HOST_PORT", "6380")

_ENV_PREFIXES = (
    "RAGENGINE_",
    "POSTGRES_",
    "REDIS_",
    "CELERY_",
    "RAGFLOW_",
    "INFERENCE_",
    "EMBEDDING_",
    "RERANKER_",
    "SESSION_",
    "GUEST_",
    "ANTHROPIC_",
    "OPENAI_",
    "GROQ_",
    "LANGFUSE_",
)


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retire la configuration héritée du shell **et du fichier `.env` réel** :
    les tests valident les défauts, pas la machine sur laquelle ils tournent.

    `Settings.model_config["env_file"]` (`".env"`) est un chemin **relatif**,
    résolu contre le répertoire de travail au moment de l'instanciation —
    depuis la racine du dépôt (là où tourne `pytest`), `Settings()` lit donc le
    vrai `.env` de ce poste. Vider `os.environ` (ci-dessous) ne suffit pas :
    pydantic-settings traite le fichier `.env` comme une source distincte, à
    laquelle il retombe dès qu'une variable est absente de l'environnement du
    process — vérifié en isolant les deux effets. Neutraliser `env_file` est
    donc la seule protection réelle, pas une prudence en plus de la première.
    """
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def _reset_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Installe un limiteur en mémoire neuf, puis ferme le singleton après chaque test.

    **Double en mémoire par défaut** (`tests/in_memory_rate_limiter.py`) : les
    plafonds de dépense LLM et le plafond de créations d'espaces invités
    (ADR-047, ADR-054) passent par ce singleton. Sans lui, un test d'API
    incrémenterait les compteurs de la base Redis `0` de la stack de
    développement réelle (`app` ci-dessous), et un test de service tenterait de
    joindre `redis://redis:6379/0`. Les tests qui éprouvent le vrai Redis
    appellent `close_rate_limiter()` dans leur propre fixture, ce qui rend la
    main au vrai client sur leur base isolée.

    `app.core.rate_limit._limiter` est un singleton **module-level**, créé
    paresseusement au premier `RateLimitMiddleware.dispatch`. Chaque test
    tourne dans sa **propre** boucle d'événements
    (`asyncio_default_fixture_loop_scope = "function"`) : sans cette remise à
    zéro en fin de test, un test qui hériterait du singleton créé par un test
    précédent réutiliserait une connexion Redis attachée à une boucle déjà
    fermée (`RuntimeError: Event loop is closed` / « attached to a different
    loop »). `close_rate_limiter()` est déjà la fonction prévue pour ça
    (`app.core.rate_limit`, appelée au `lifespan` en production) — elle est
    sans effet si le singleton n'a jamais été créé.
    """
    monkeypatch.setattr(rate_limit_module, "_limiter", InMemoryRateLimiter())
    yield
    await close_rate_limiter()


@dataclass(slots=True)
class DispatchedTasks:
    """Publications Celery interceptées : `(nom de tâche, *identifiants)`.

    Variadique — pas `(str, str)` figé — parce que les tâches publiées ici
    n'ont pas toutes la même arité : `PRECHECK_TASK`/`CHUNKING_TASK`
    (ingestion) et la reprise de job (ADR-020) ne prennent qu'un `job_id`,
    `RUN_ASYNC_EVALUATION_TASK` (ADR-028) prend `knowledge_base_id` **et**
    `evaluation_run_id` (`run_async_evaluation`, `app.workers.tasks.
    evaluations`). Une arité figée à deux masquerait exactement le genre
    d'écart trouvé en campagne E2E entre `EvaluationService.
    _publish_after_commit` et la tâche réelle qu'il publie — invisible ici
    tant que rien ne force `delay(...)` à retomber sur le nombre d'arguments
    réellement attendu par chaque tâche.
    """

    calls: list[tuple[str, ...]] = field(default_factory=list)
    _pending: str = ""

    def __call__(self, task_name: str) -> DispatchedTasks:
        """Substitut de `task_by_name` : retient le nom, se rend elle-même."""
        self._pending = task_name
        return self

    def delay(self, *args: str) -> None:
        """Substitut de `Task.delay` : note au lieu de publier."""
        self.calls.append((self._pending, *args))

    @property
    def names(self) -> list[str]:
        """Noms des tâches publiées, dans l'ordre."""
        return [call[0] for call in self.calls]


@pytest.fixture(autouse=True)
def dispatched_tasks(monkeypatch: pytest.MonkeyPatch) -> DispatchedTasks:
    """Garde-fou : **aucun test ne publie sur un vrai broker**.

    Depuis que l'ingestion réveille elle-même le pipeline, `DocumentService`
    publie une tâche Celery à chaque enregistrement et à chaque décision de
    precheck ; `IngestionJobService` en fait autant depuis `retry` (Phase 9,
    ADR-020) ; `EvaluationService.create_pending_run` en fait autant pour un
    run asynchrone (Phase 12 Étape 4, item restant, ADR-028). Sans cette
    interception, toute la suite exigerait un Redis joignable pour des cas
    qui n'ont rien à voir avec la file.

    Seule la **résolution de la tâche** est remplacée, dans le seul espace de noms
    de chaque service : la logique qui décide *quand* publier — et surtout le commit qui
    doit la précéder — reste intégralement exécutée. Un test qui remplacerait la
    méthode de publication entière supprimerait précisément le code dont l'instant
    d'exécution fait la correction (voir `tests/api/v1/test_pipeline_dispatch.py`).
    Les trois services partagent le même enregistreur : l'ordre relatif de leurs
    publications, s'il devait un jour compter, resterait observable via
    `DispatchedTasks.calls`. Les tâches Celery elles-mêmes ne sont pas touchées :
    leur propre chaînage passe par `app.workers.tasks.ingestion`/
    `app.workers.tasks.evaluations`, des espaces de noms distincts.
    """
    recorder = DispatchedTasks()
    monkeypatch.setattr(documents_service, "task_by_name", recorder)
    monkeypatch.setattr(jobs_service, "task_by_name", recorder)
    monkeypatch.setattr(evaluations_service, "task_by_name", recorder)
    return recorder


@pytest.fixture
def app(_isolated_environment: None, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Application neuve par test, construite sans fichier `.env`.

    Deux réglages Redis, pour deux raisons distinctes :

    - `REDIS_URL` réécrit vers le port publié sur l'hôte par
      `docker/docker-compose.yml` (voir `_REDIS_HOST_PORT` ci-dessus) : le nom
      de service Docker `redis` par défaut n'est pas résolvable depuis l'hôte,
      et son échec de résolution DNS coûte plusieurs secondes par tentative.
    - `RAGENGINE_RATE_LIMIT_ENABLED=false` : le rate limiting réel est du
      ressort exclusif de `tests/test_rate_limit.py` (sa propre application
      Starlette, sa propre base Redis `9`). Aucun autre test de la suite
      n'attend ni ne vérifie de `429` — en laisser le limiteur actif ici
      ferait dépendre le nombre de requêtes qu'un test peut émettre du
      contenu, encore accumulé en base Redis `0`, de tous les tests exécutés
      avant lui dans le même process, ce qui est exactement le défaut que
      l'« échec ouvert » masquait silencieusement tant que `REDIS_URL` ne
      résolvait pas.
    """
    monkeypatch.setenv("REDIS_URL", f"redis://127.0.0.1:{_REDIS_HOST_PORT}/0")
    monkeypatch.setenv("RAGENGINE_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client httpx branché directement sur l'application ASGI (pas de socket)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as http_client:
        yield http_client


@pytest.fixture
def explicit_llm_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pose les plafonds de la démo (20 / 24 / 10 $ / `claude-haiku-4-5`) par l'environnement.

    Le cœur seul n'en a aucun par défaut (ADR-054, addendum 2026-10-04) ; un greffon peut
    en fournir. Les tests du *mécanisme* de plafonnement (compteurs, ordre, échec fermé) le
    posent explicitement : ils passent alors à l'identique avec ou sans greffon.
    """
    monkeypatch.setenv("RAGENGINE_PLATFORM_LLM_MODEL_ID", "claude-haiku-4-5")
    monkeypatch.setenv("RAGENGINE_PLATFORM_LLM_USER_DAILY_LIMIT", "20")
    monkeypatch.setenv("RAGENGINE_PLATFORM_LLM_EVALUATION_DAILY_LIMIT", "24")
    monkeypatch.setenv("RAGENGINE_PLATFORM_LLM_DAILY_BUDGET_USD", "10.0")
    get_settings.cache_clear()


@pytest.fixture
def without_plugins() -> Iterator[None]:
    """Aucun greffon pour toute la durée du test (requêtes comprises)."""
    with plugins_overridden(()):
        yield


@pytest.fixture
def core_app(
    without_plugins: None,
    app: FastAPI,
    db_session: AsyncSession,  # noqa: F811
) -> FastAPI:
    """Application du cœur seul, sur la session du test (aucun commit)."""
    del app  # environnement isolé et Redis réaiguillé par la fixture `app`
    core = create_app()

    async def _test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    core.dependency_overrides[get_db_session] = _test_session
    return core


@pytest.fixture
async def core_client(core_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=core_app), base_url=BASE_URL) as client:
        yield client
