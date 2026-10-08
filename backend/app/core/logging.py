"""Logging structuré JSON pour l'ensemble des process (API et workers).

Une seule sortie : `stdout`, une ligne JSON par événement, collectée par Docker.
L'usage de `print` est proscrit (CONTRIBUTING.md §6).
"""

from __future__ import annotations

import logging
import sys
from typing import Final

from pythonjsonlogger.core import RESERVED_ATTRS
from pythonjsonlogger.json import JsonFormatter

_LOG_RECORD_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s %(message)s"

# `color_message` est un doublon coloré (ANSI) du message, ajouté par uvicorn :
# il n'a aucun sens dans une sortie JSON destinée à un collecteur de logs.
_IGNORED_ATTRS: Final = [*RESERVED_ATTRS, "color_message"]

_RENAMED_FIELDS: Final = {
    "asctime": "timestamp",
    "levelname": "level",
    "name": "logger",
}

_PROPAGATED_LOGGERS: Final = ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "alembic")


def build_formatter() -> JsonFormatter:
    """Construit le formateur JSON commun à tous les handlers."""
    return JsonFormatter(
        _LOG_RECORD_FORMAT,
        rename_fields=_RENAMED_FIELDS,
        reserved_attrs=_IGNORED_ATTRS,
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        # Corpus francophone : les accents restent lisibles dans les logs.
        json_ensure_ascii=False,
    )


def configure_logging(level: str = "INFO") -> None:
    """Installe le handler JSON sur le logger racine.

    Idempotent : les handlers existants sont remplacés, et les loggers des
    bibliothèques tierces (uvicorn, celery…) sont ramenés sur le logger racine
    pour garantir un format unique.

    Args:
        level: Seuil de journalisation, piloté par `RAGENGINE_LOG_LEVEL`.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(build_formatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for name in _PROPAGATED_LOGGERS:
        third_party = logging.getLogger(name)
        third_party.handlers.clear()
        third_party.propagate = True
