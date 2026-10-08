"""Endpoint de supervision `GET /api/v1/health`."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app import __version__
from app.core.config import Environment, Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """État de santé de la façade."""

    status: Literal["ok"] = Field(default="ok", description="Disponibilité de la façade.")
    version: str = Field(description="Version de la façade RAGEngine.")
    environment: Environment = Field(description="Environnement d'exécution déclaré.")


@router.get(
    "/health",
    summary="État de santé de la façade",
    description="Sonde de liveness utilisée par Docker et l'orchestrateur.",
)
async def read_health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Renvoie l'état de la façade sans solliciter de dépendance externe."""
    return HealthResponse(version=__version__, environment=settings.environment)
