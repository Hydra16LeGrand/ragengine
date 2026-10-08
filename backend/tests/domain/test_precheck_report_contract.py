"""Contrat du rapport de precheck servi par l'API (ADR-070).

`DocumentRead.precheck_report` est typé, mais ce qu'il sert ne change pas d'un octet par
rapport au dictionnaire libre d'avant : un rapport ancien, sans les champs ajoutés depuis,
n'en reçoit aucun par défaut, et l'ordre des clés relues est conservé.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain.schemas.document import DocumentRead
from app.domain.schemas.precheck import PrecheckFormat, PrecheckReport, PrecheckReportRead
from app.main import create_app

CURRENT: dict[str, Any] = {
    "supported": True,
    "format": "pdf",
    "size_bytes": 1048576,
    "page_count": 12,
    "analyzed_page_count": 12,
    "text_character_count": 18234,
    "is_likely_scanned": False,
    "scanned_page_ratio": 0.0,
    "table_count": 3,
    "figure_count": 1,
    "two_column_page_count": 2,
    "detected_languages": ["fr", "en"],
    "error": None,
}
LEGACY = {key: value for key, value in CURRENT.items() if key != "two_column_page_count"}
DEGRADED: dict[str, Any] = {
    **LEGACY,
    "supported": False,
    "format": "unsupported",
    "page_count": None,
    "scanned_page_ratio": None,
    "error": {"code": "unsupported_format", "message": "Format non pris en charge."},
}
# Ordre de clés tel que le rend le JSONB de PostgreSQL (longueur, puis ordre alphabétique).
JSONB_ORDER = dict(sorted(CURRENT.items(), key=lambda item: (len(item[0]), item[0])))

_ADAPTER = TypeAdapter(PrecheckReportRead | None)


def _served(stored: dict[str, Any]) -> str:
    """Ce que l'API écrit : validation du rapport relu, puis sérialisation JSON compacte."""
    payload = _ADAPTER.dump_python(_ADAPTER.validate_python(stored), mode="json")
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _before(stored: dict[str, Any]) -> str:
    """Ce que l'API servait quand le champ était un `dict[str, Any]`."""
    return json.dumps(stored, separators=(",", ":"), ensure_ascii=False)


@pytest.mark.parametrize("stored", [CURRENT, LEGACY, DEGRADED, JSONB_ORDER])
def test_a_stored_report_is_served_byte_for_byte(stored: dict[str, Any]) -> None:
    assert _served(stored) == _before(stored)


def test_a_legacy_report_gets_no_default_field() -> None:
    assert "two_column_page_count" not in json.loads(_served(LEGACY))


def test_the_same_holds_inside_document_read() -> None:
    document = {
        "id": "00000000-0000-0000-0000-000000000001",
        "knowledge_base_id": "00000000-0000-0000-0000-000000000002",
        "filename": "a.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1,
        "checksum_sha256": "0" * 64,
        "storage_uri": "file:///a.pdf",
        "status": "pending",
        "created_at": "2026-10-04T10:00:00Z",
        "updated_at": "2026-10-04T10:00:00Z",
        "precheck_report": LEGACY,
    }

    served = DocumentRead.model_validate(document).model_dump(mode="json")

    assert served["precheck_report"] == LEGACY
    assert list(served["precheck_report"]) == list(LEGACY)


def test_a_missing_report_stays_null() -> None:
    assert _ADAPTER.dump_python(_ADAPTER.validate_python(None), mode="json") is None


def test_the_producer_still_writes_every_field() -> None:
    """`PrecheckReport` (écriture) garde ses défauts : seul le modèle de lecture s'efface."""
    written = PrecheckReport(supported=True, format=PrecheckFormat.PDF, size_bytes=1)

    assert set(written.model_dump(mode="json")) == set(CURRENT)


@pytest.mark.parametrize(
    "broken",
    [
        {"supported": True, "format": "inconnu", "size_bytes": 1},
        {"format": "pdf", "size_bytes": 1},
        {**CURRENT, "table_count": -1},
    ],
)
def test_a_report_that_breaks_the_schema_is_rejected(broken: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PrecheckReportRead.model_validate(broken)


def test_the_openapi_publishes_the_report_schema() -> None:
    schemas = create_app().openapi()["components"]["schemas"]

    report = schemas["PrecheckReportRead"]
    assert set(report["required"]) == {"supported", "format", "size_bytes"}
    assert set(report["properties"]) == set(CURRENT)
    assert set(schemas["PrecheckFormat"]["enum"]) >= {"pdf", "spreadsheet", "unsupported"}
    reference = json.dumps(schemas["DocumentRead"]["properties"]["precheck_report"])
    assert "PrecheckReportRead" in reference
