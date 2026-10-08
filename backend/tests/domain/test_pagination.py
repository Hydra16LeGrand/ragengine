"""Curseur de pagination : aller-retour, robustesse et découpage de page."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pytest

from app.core.errors import ValidationError
from app.domain.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Cursor,
    Page,
    PaginationParams,
    build_page,
)

MOMENT = datetime(2026, 7, 25, 18, 30, 45, 123456, tzinfo=UTC)
IDENTIFIER = uuid.UUID("11111111-2222-3333-4444-555555555555")


class Row(NamedTuple):
    """Ligne minimale exposant le contrat `(created_at, id)`."""

    created_at: datetime
    id: uuid.UUID


def make_rows(count: int) -> list[Row]:
    """Lignes triées du plus récent au plus ancien, comme les renvoie la requête."""
    return [
        Row(created_at=MOMENT - timedelta(seconds=index), id=uuid.uuid4()) for index in range(count)
    ]


def test_cursor_roundtrip_preserves_position() -> None:
    """Encoder puis décoder restitue exactement le couple `(created_at, id)`."""
    cursor = Cursor(created_at=MOMENT, id=IDENTIFIER)

    decoded = Cursor.decode(cursor.encode())

    assert decoded.created_at == MOMENT
    assert decoded.id == IDENTIFIER


def test_cursor_is_opaque_base64url_without_padding() -> None:
    """Le curseur voyage dans une query string : pas de `=`, `+` ni `/`."""
    encoded = Cursor(created_at=MOMENT, id=IDENTIFIER).encode()

    assert "=" not in encoded
    assert "+" not in encoded
    assert "/" not in encoded


def test_cursor_normalizes_to_utc() -> None:
    """Un instant exprimé dans un autre fuseau revient en UTC, sans décalage."""
    paris_noon = datetime(2026, 7, 25, 14, 0, tzinfo=UTC).astimezone()

    decoded = Cursor.decode(Cursor(created_at=paris_noon, id=IDENTIFIER).encode())

    assert decoded.created_at == paris_noon


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "!!!",
        "n/a",
        base64.urlsafe_b64encode(b"pas-de-separateur").decode().rstrip("="),
        base64.urlsafe_b64encode(b"2026-07-25T18:30:45+00:00|pas-un-uuid").decode().rstrip("="),
        base64.urlsafe_b64encode(b"pas-une-date|11111111-2222-3333-4444-555555555555")
        .decode()
        .rstrip("="),
        base64.urlsafe_b64encode(b"\xff\xfe").decode().rstrip("="),
    ],
    ids=[
        "vide",
        "non-base64",
        "caracteres-interdits",
        "sans-separateur",
        "uuid-invalide",
        "date-invalide",
        "octets-non-utf8",
    ],
)
def test_corrupted_cursor_raises_validation_error(raw: str) -> None:
    """Un curseur forgé produit un 422, jamais une 500."""
    with pytest.raises(ValidationError) as excinfo:
        Cursor.decode(raw)

    assert excinfo.value.to_problem().status == 422


def test_truncated_cursor_raises_validation_error() -> None:
    """Un curseur tronqué en cours de route est rejeté proprement."""
    encoded = Cursor(created_at=MOMENT, id=IDENTIFIER).encode()

    with pytest.raises(ValidationError):
        Cursor.decode(encoded[:-8])


def test_pagination_params_decode_absent_cursor() -> None:
    """Première page : aucun curseur à décoder."""
    params = PaginationParams()

    assert params.limit == DEFAULT_PAGE_SIZE
    assert params.decoded_cursor() is None


def test_pagination_params_reject_out_of_range_limit() -> None:
    """`limit` reste borné à 1..100 par le schéma lui-même."""
    with pytest.raises(ValueError, match="less than or equal"):
        PaginationParams(limit=MAX_PAGE_SIZE + 1)


def test_build_page_trims_the_sentinel_row_and_emits_a_cursor() -> None:
    """La ligne excédentaire signale une page suivante et n'est pas rendue."""
    rows = make_rows(4)

    page = build_page(rows, serializer=lambda row: row.id, limit=3)

    assert isinstance(page, Page)
    assert page.data == [row.id for row in rows[:3]]
    assert page.next_cursor is not None
    resumed = Cursor.decode(page.next_cursor)
    assert (resumed.created_at, resumed.id) == (rows[2].created_at, rows[2].id)


def test_build_page_on_last_page_has_no_cursor() -> None:
    """Sans ligne sentinelle, la page est la dernière : `next_cursor` est `null`."""
    rows = make_rows(2)

    page = build_page(rows, serializer=lambda row: row.id, limit=3)

    assert page.data == [row.id for row in rows]
    assert page.next_cursor is None


def test_build_page_on_empty_result() -> None:
    """Une collection vide reste une réponse valide, pas une erreur."""
    page = build_page([], serializer=lambda row: row.id, limit=10)

    assert page.data == []
    assert page.next_cursor is None


def test_build_page_rejects_an_out_of_range_limit() -> None:
    """Une taille de page hors bornes est refusée, pas rognée en silence."""
    with pytest.raises(ValidationError):
        build_page(make_rows(1), serializer=lambda row: row.id, limit=MAX_PAGE_SIZE + 1)
