"""Tests du logging structuré JSON."""

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from app.core.logging import configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    """Rétablit l'état du logger racine, muté par `configure_logging`."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    root.handlers = handlers
    root.setLevel(level)


def _last_record(captured: str) -> dict[str, Any]:
    lines = [line for line in captured.splitlines() if line.strip()]
    assert lines, "aucune ligne de log capturée"
    payload: dict[str, Any] = json.loads(lines[-1])
    return payload


def test_records_are_emitted_as_json_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Chaque événement est une ligne JSON portant ses champs contextuels."""
    configure_logging("INFO")
    logging.getLogger("app.tests").info("ingestion démarrée", extra={"kb_id": "kb_1"})

    record = _last_record(capsys.readouterr().out)

    assert record["level"] == "INFO"
    assert record["logger"] == "app.tests"
    assert record["message"] == "ingestion démarrée"
    assert record["kb_id"] == "kb_1"
    assert record["timestamp"]


def test_level_is_driven_by_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    """`RAGENGINE_LOG_LEVEL` pilote le seuil du logger racine."""
    configure_logging("WARNING")
    logging.getLogger("app.tests").info("message ignoré")

    assert capsys.readouterr().out == ""


def test_uvicorn_color_message_is_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    """Le doublon coloré ajouté par uvicorn ne pollue pas la sortie JSON."""
    configure_logging("INFO")
    logging.getLogger("uvicorn.error").info("démarrage", extra={"color_message": "\x1b[36m"})

    record = _last_record(capsys.readouterr().out)

    assert "color_message" not in record
