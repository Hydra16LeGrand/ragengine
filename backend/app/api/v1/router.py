"""Agrégation des routers du cœur de l'API v1, monté par `create_app` sous `/api/v1`.

Les routeurs des greffons (`app.core.plugins.plugin_routers`) sont montés à
côté par `create_app`, après ceux-ci : le cœur n'en nomme aucun.
"""

from fastapi import APIRouter

from app.api.v1.routes import (
    api_keys,
    auth,
    documents,
    evaluations,
    health,
    jobs,
    knowledge_bases,
    llm_keys,
    organizations,
    providers,
    retrieval,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(api_keys.router)
router.include_router(llm_keys.router)
router.include_router(organizations.router)
router.include_router(knowledge_bases.router)
router.include_router(documents.router)
router.include_router(jobs.router)
router.include_router(providers.router)
router.include_router(retrieval.router)
router.include_router(evaluations.router)
