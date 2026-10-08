"""Schéma du rapport de precheck — ré-export depuis le domaine (ADR-070).

Le schéma vit dans `app/domain/schemas/precheck.py` : l'API en expose la forme dans l'OpenAPI,
et `app/domain` ne dépend pas de `app/ingestion`. Ce module garde les chemins d'import du
precheck et de ses consommateurs.
"""

from __future__ import annotations

from app.domain.schemas.precheck import (
    PrecheckError,
    PrecheckErrorCode,
    PrecheckFormat,
    PrecheckReport,
)

__all__ = ["PrecheckError", "PrecheckErrorCode", "PrecheckFormat", "PrecheckReport"]
