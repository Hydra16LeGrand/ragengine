"""Greffon d'exemple minimal : une route et un réglage par défaut (`docs/extending.md`).

Ce que montre ce greffon, et rien de plus :

- `routers` : une route `GET /api/v1/example/ping`, montée sous `/api/v1` après celles du cœur ;
- `settings_defaults` : une valeur par défaut de réglage (`app_name`), appliquée **seulement** si
  l'environnement ne la renseigne pas.

Il n'est pas actif par défaut : il vit hors de `app/plugins/`, le seul paquet que le cœur
découvre. Pour l'activer, copier ce dossier sous `app/plugins/` (depuis `backend/`). Il n'utilise
que des imports relatifs, justement pour que la copie fonctionne sans rien éditer.

Le module reste léger à l'import (`get_plugins()` l'importe très tôt) : le routeur, qui charge
FastAPI et les dépendances de l'API, n'est importé que dans la fabrique `_routers`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.core.plugins import Plugin

if TYPE_CHECKING:
    from fastapi import APIRouter

APP_NAME: str = "RAGEngine — exemple de greffon"
"""Marque posée par défaut tant que `RAGENGINE_APP_NAME` n'est pas renseignée."""


def _routers() -> Sequence[APIRouter]:
    from .routes import router  # noqa: TID252 - relatif : le dossier se copie tel quel

    return (router,)


plugin = Plugin(
    name="ping",
    routers=_routers,
    settings_defaults={"app_name": APP_NAME},
)
"""Enregistrement du greffon : un attribut `plugin`, instance de `app.core.plugins.Plugin`."""
