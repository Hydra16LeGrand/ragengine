"""Precheck : analyse préalable et légère d'un document, avant toute ingestion.

Produit le rapport soumis à la validation humaine du Schéma A (CONTRIBUTING.md §1),
**sans jamais appeler le moteur d'ingestion** ni le service d'inférence GPU :
RAGFlow n'analyse un document qu'après qu'on lui ait imposé une méthode de
découpage, il ne peut donc pas répondre à « à quoi ressemble ce document ? ».

Point d'entrée : `analyze_document` (async) ou `analyze_document_sync` (Celery).
Le résultat, un `PrecheckReport`, est sérialisé dans `Document.precheck_report`
et son champ `detected_languages` alimente la colonne du même nom (ADR-003).
"""

from app.ingestion.precheck.analyzer import analyze_document, analyze_document_sync
from app.ingestion.precheck.schemas import (
    PrecheckError,
    PrecheckErrorCode,
    PrecheckFormat,
    PrecheckReport,
)

__all__ = [
    "PrecheckError",
    "PrecheckErrorCode",
    "PrecheckFormat",
    "PrecheckReport",
    "analyze_document",
    "analyze_document_sync",
]
