"""Référence développeur publique : schéma OpenAPI filtré + page Redoc.

Distincte de `docs_enabled` (`app/main.py`) : ce dernier gate le Swagger/Redoc
**complets** (toute la surface de l'API, y compris l'administration interne)
au seul environnement de développement — une exposition en production serait
une fuite de reconnaissance. Ce module sert l'inverse : un sous-ensemble de
routes explicitement jugées pertinentes pour un développeur externe qui
intègre l'API (les deux parcours de référence, l'authentification, les
providers, les évaluations), volontairement disponible dans **tous les
environnements**, y compris la production — c'est tout l'intérêt de filtrer
plutôt que de tout masquer.

Le filtre vit sur les tags déjà posés par chaque routeur
(`app/api/v1/routes/*.py`), pas sur une liste de chemins à maintenir en
double : ajouter une route à un routeur déjà inclus dans `PUBLIC_DOC_TAGS`
l'expose automatiquement ici, sans y repenser.
"""

import copy
import html
import re
from typing import Any, Final

from fastapi import APIRouter, FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import JsonValue

from app.core.config import get_settings

_SCHEMA_REF_RE: Final = re.compile(r"^#/components/schemas/(?P<name>.+)$")

PUBLIC_DOC_TAGS: Final[frozenset[str]] = frozenset(
    {
        "identity",
        "api-keys",
        "knowledge-bases",
        "documents",
        "jobs",
        "retrieval",
        "providers",
        "evaluations",
    }
)
"""Tags exposés à la référence publique — cf. `docs/api-guide.md`, dont le
périmètre (auth, Schéma A, Schéma B, providers, bases+permissions,
évaluations) est ici traduit en filtre exécutable plutôt que redit à la main.

Volontairement absent : `organizations` (administration de compte/équipe
côté interface web, pas une surface d'intégration développeur) et `health`
(sonde de liveness, sans valeur d'intégration)."""


def _referenced_schema_names(node: JsonValue) -> set[str]:
    """Noms de schémas `#/components/schemas/...` référencés, récursivement.

    `app.include_router` (FastAPI ≥ 0.140) enregistre chaque routeur inclus
    comme un `_IncludedRouter` paresseux dans `app.routes` : filtrer par
    `isinstance(route, APIRoute)` avant de construire le schéma n'y trouve
    plus rien (vérifié — 0 route). `get_openapi(routes=app.routes, ...)`, en
    revanche, sait déplier cette structure (c'est l'appel que fait
    `FastAPI.openapi()` lui-même) : on construit donc le schéma **complet**
    d'abord, puis on le filtre en JSON — chemin plus robuste aux détails
    internes de routage que de rejouer la résolution des routeurs à la main.
    """
    names: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            match = _SCHEMA_REF_RE.match(ref)
            if match is not None:
                names.add(match.group("name"))
        for value in node.values():
            names |= _referenced_schema_names(value)
    elif isinstance(node, list):
        for item in node:
            names |= _referenced_schema_names(item)
    return names


def build_public_openapi_schema(app: FastAPI) -> dict[str, Any]:
    """Sous-ensemble du schéma OpenAPI complet, restreint à `PUBLIC_DOC_TAGS`.

    Deux passes : (1) ne garder, par chemin, que les opérations dont au moins
    un tag est public — un chemin sans opération restante disparaît ; (2) ne
    garder dans `components.schemas` que ce qui reste atteignable par `$ref`
    depuis les opérations conservées, transitivement (un schéma public qui
    référence lui-même un autre schéma public doit rester résolvable). Sans
    cette seconde passe, un schéma de réponse propre à `organizations` par
    exemple pourrait rester visible dans `components` sans qu'aucun chemin
    public n'y renvoie — fuite d'information sur le modèle interne, pas une
    route accessible, mais une fuite quand même.
    """
    full_schema = get_openapi(
        title=f"{app.title} — Référence développeur",
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema = copy.deepcopy(full_schema)

    filtered_paths: dict[str, Any] = {}
    for path, operations in schema.get("paths", {}).items():
        kept_operations = {
            method: operation
            for method, operation in operations.items()
            if PUBLIC_DOC_TAGS.intersection(operation.get("tags", []))
        }
        if kept_operations:
            filtered_paths[path] = kept_operations
    schema["paths"] = filtered_paths

    reachable = _referenced_schema_names(filtered_paths)
    all_schemas = schema.get("components", {}).get("schemas", {})
    frontier = reachable
    while frontier:
        newly_reachable: set[str] = set()
        for name in frontier:
            newly_reachable |= _referenced_schema_names(all_schemas.get(name, {}))
        frontier = newly_reachable - reachable
        reachable |= newly_reachable

    if "components" in schema:
        schema["components"]["schemas"] = {
            name: definition for name, definition in all_schemas.items() if name in reachable
        }

    return schema


_REDOC_HTML: Final = """<!doctype html>
<html>
  <head>
    <title>{app_name} — Référence API</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>body {{ margin: 0; padding: 0; }}</style>
  </head>
  <body>
    <redoc spec-url="{spec_url}"></redoc>
    <script src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"></script>
  </body>
</html>
"""


def build_public_docs_router(app: FastAPI, *, api_prefix: str) -> APIRouter:
    """Deux routes, montées sans garde d'environnement (`app/main.py`).

    Prend `app` en paramètre plutôt que d'importer le singleton de `main.py` :
    ce module est monté **pendant** la construction de `create_app()`, avant
    que `app` n'existe au niveau module de `main.py` — fermer sur la référence
    reçue ici évite tout cycle d'import. `api_prefix` évite de recalculer
    `API_PREFIX` et le risque de dérive avec l'URL que Redoc interroge.
    """
    router = APIRouter(include_in_schema=False)
    openapi_path = f"{api_prefix}/openapi-public.json"

    @router.get(openapi_path)
    async def public_openapi_schema() -> JSONResponse:
        return JSONResponse(build_public_openapi_schema(app))

    @router.get(f"{api_prefix}/docs-public", response_class=HTMLResponse)
    async def public_docs_page() -> HTMLResponse:
        return HTMLResponse(
            _REDOC_HTML.format(spec_url=openapi_path, app_name=html.escape(get_settings().app_name))
        )

    return router
