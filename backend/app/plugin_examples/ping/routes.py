"""Route du greffon d'exemple : `GET /api/v1/example/ping`."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.v1.dependencies import RegisteredPrincipal, SettingsDep

router = APIRouter(prefix="/example", tags=["example"])


class Pong(BaseModel):
    """Corps de réponse de `GET /api/v1/example/ping`."""

    pong: bool
    app_name: str
    """Lu dans `Settings` : défaut du greffon tant que l'environnement ne le surcharge pas."""


@router.get("/ping", response_model=Pong)
async def ping(settings: SettingsDep, principal: RegisteredPrincipal) -> Pong:
    """Répond `pong` à un appelant connecté — l'exemple le plus court d'une route de greffon."""
    del principal  # la dépendance suffit : elle répond 401 sans connexion
    return Pong(pong=True, app_name=settings.app_name)
