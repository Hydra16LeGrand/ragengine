"""Le guide d'API public ne décrit que les routes du cœur (ADR-069).

Garde-fou contre la dérive : tout `VERBE /chemin` cité dans `docs/api-guide.md` doit exister
dans l'OpenAPI du cœur seul (`python -m app.cli.openapi --core`), sans greffon. Une route de
plateforme (mode invité, chatbots, administration) ou une route disparue y fait échouer le test.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI

GUIDE = Path(__file__).resolve().parents[2] / "docs" / "api-guide.md"
API_PREFIX = "/api/v1"
VERBS = "GET|POST|PUT|PATCH|DELETE"
CITATION = re.compile(rf"\b((?:{VERBS})(?:/(?:{VERBS}))*)\s+(/[A-Za-z0-9_\-/{{}}.\[\]]+)")
# Servies par FastAPI en développement : absentes du schéma qu'elles décrivent.
SERVED_BY_FASTAPI = {"/docs", "/redoc", "/openapi.json", "/docs-public"}


def _normalize(path: str) -> str:
    path = path.rstrip(".,;:")
    path = path.removeprefix(API_PREFIX)
    return re.sub(r"\{[^}]*\}", "{}", path)


def _cited_routes(text: str) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for verbs, raw_path in CITATION.findall(text):
        # `/knowledge-bases[/{kb_id}]` cite deux routes : avec et sans le segment optionnel.
        variants = (
            [re.sub(r"\[[^\]]*\]", "", raw_path), raw_path.replace("[", "").replace("]", "")]
            if "[" in raw_path
            else [raw_path]
        )
        for variant in variants:
            for verb in verbs.split("/"):
                routes.add((verb, _normalize(variant)))
    return routes


def _core_routes(app: FastAPI) -> set[tuple[str, str]]:
    schema = app.openapi()
    return {
        (method.upper(), _normalize(path))
        for path, operations in schema["paths"].items()
        for method in operations
        if method != "parameters"
    }


def test_the_public_guide_only_cites_routes_of_the_core(core_app: FastAPI) -> None:
    cited = _cited_routes(GUIDE.read_text(encoding="utf-8"))
    assert cited, "le guide ne cite aucune route : l'extraction est cassée"

    unknown = sorted(
        f"{verb} {path}"
        for verb, path in cited - _core_routes(core_app)
        if path not in SERVED_BY_FASTAPI
    )

    assert not unknown, f"routes du guide absentes de l'OpenAPI du cœur : {unknown}"


def test_the_extraction_catches_a_route_the_core_does_not_serve(core_app: FastAPI) -> None:
    cited = _cited_routes("`POST /api/v1/guest-sessions` et `PUT /me/credentials`")

    assert cited - _core_routes(core_app) == {
        ("POST", "/guest-sessions"),
        ("PUT", "/me/credentials"),
    }
