"""Point d'entrée ASGI de la façade RAGEngine.

`create_app()` est la seule fabrique de l'application : elle configure le
logging, branche le contrat d'erreur RFC 9457, le CORS et monte l'API v1.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import API_VERSION, __version__
from app.api.public_docs import build_public_docs_router
from app.api.v1.router import router as api_v1_router
from app.core.body_limit import RequestBodyLimitMiddleware
from app.core.config import Environment, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.plugins import get_plugins, plugin_routers
from app.core.rate_limit import RateLimitMiddleware, close_rate_limiter
from app.core.security_headers import SecurityHeadersMiddleware
from app.db.session import dispose_engine

_CORS_ALLOWED_METHODS: Final = ["GET", "POST", "PUT", "PATCH", "DELETE"]
"""Verbes effectivement exposés par l'API v1 — jamais `["*"]` (durcissement §9)."""

_CORS_ALLOWED_HEADERS: Final = ["Authorization", "Content-Type", "Accept"]
"""Les seuls en-têtes que le SPA ou un client à clé API ont besoin de poser."""

API_PREFIX: Final = f"/api/{API_VERSION}"

_logger = logging.getLogger(__name__)

_DESCRIPTION: Final = (
    "API REST de {app_name}, système RAG souverain : gestion des bases de "
    "connaissances, ingestion avec precheck human-in-the-loop, et retrieval "
    "hybride. Les erreurs suivent la RFC 9457 (`application/problem+json`)."
)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ferme le pool de connexions PostgreSQL et Redis à l'arrêt du process."""
    del app
    yield
    await dispose_engine()
    await close_rate_limiter()


def create_app() -> FastAPI:
    """Construit et configure l'application FastAPI."""
    settings = get_settings()
    configure_logging(settings.log_level)

    # Swagger/ReDoc/le schéma brut n'ont pas leur place hors développement : ce
    # sont des routes non authentifiées qui exposent toute la surface de l'API.
    # `None` les retire complètement (FastAPI ne les enregistre même pas), pas
    # seulement un masquage côté client.
    docs_enabled = settings.environment is Environment.DEVELOPMENT
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=API_VERSION,
        description=_DESCRIPTION.format(app_name=settings.app_name),
        openapi_url=f"{API_PREFIX}/openapi.json" if docs_enabled else None,
        docs_url=f"{API_PREFIX}/docs" if docs_enabled else None,
        redoc_url=f"{API_PREFIX}/redoc" if docs_enabled else None,
        lifespan=_lifespan,
    )

    register_exception_handlers(app)

    # Ordre d'ajout délibéré : Starlette empile les middlewares utilisateur du
    # plus interne (premier `add_middleware`) au plus externe (dernier). Une
    # requête traverse donc, à l'aller, `SecurityHeadersMiddleware` → `CORS` →
    # `RateLimitMiddleware` → `RequestBodyLimitMiddleware` → le routeur ; au
    # retour, dans l'ordre inverse. En ajoutant ces deux middlewares en premier
    # (donc les plus internes), un `429`/`413` qu'ils construisent eux-mêmes —
    # sans jamais atteindre le routeur — remonte quand même à travers `CORS`
    # (l'origine autorisée reste annoncée) et `SecurityHeadersMiddleware` (les
    # en-têtes durcis restent posés), exactement comme une réponse `200`
    # ordinaire.
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(RequestBodyLimitMiddleware, settings=settings)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=_CORS_ALLOWED_METHODS,
            allow_headers=_CORS_ALLOWED_HEADERS,
        )

    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(api_v1_router, prefix=API_PREFIX)
    # Greffons (ADR-069) : montés après le cœur, sous le même préfixe. Sans
    # greffon, l'application ne sert que les routes du cœur.
    for plugin_router in plugin_routers():
        app.include_router(plugin_router, prefix=API_PREFIX)

    # Référence développeur publique (schéma filtré + Redoc) : intentionnellement
    # non gatée par `docs_enabled` — c'est le sous-ensemble jugé sûr en
    # production, cf. `app/api/public_docs.py`.
    app.include_router(build_public_docs_router(app, api_prefix=API_PREFIX))

    _logger.info(
        "Façade RAGEngine initialisée",
        extra={
            "version": __version__,
            "environment": settings.environment.value,
            "plugins": [plugin.name for plugin in get_plugins()],
        },
    )

    return app


app = create_app()
"""Instance ASGI servie par uvicorn (`app.main:app`)."""
