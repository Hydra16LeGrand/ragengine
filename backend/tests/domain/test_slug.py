"""Dérivation des slugs — une seule règle pour tous les appelants."""

from __future__ import annotations

import re

import pytest

from app.domain.slug import SLUG_PATTERN, slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Référentiel Qualité", "referentiel-qualite"),
        ("  Support   Technique  ", "support-technique"),
        ("Base #1 (2026)", "base-1-2026"),
        ("déjà-un-slug", "deja-un-slug"),
        ("ÉCOLE", "ecole"),
        ("a---b", "a-b"),
    ],
)
def test_slugify_produces_a_canonical_slug(name: str, expected: str) -> None:
    """Les diacritiques sont translittérés, jamais remplacés par des tirets."""
    slug = slugify(name)

    assert slug == expected
    assert re.match(SLUG_PATTERN, slug)


def test_slugify_truncates_without_leaving_a_trailing_dash() -> None:
    """Une troncature au milieu d'un tiret produirait un slug invalide."""
    slug = slugify("mot " * 40, max_length=15)

    assert len(slug) <= 15
    assert re.match(SLUG_PATTERN, slug)


def test_slugify_returns_an_empty_string_when_nothing_is_usable() -> None:
    """Un nom sans caractère exploitable laisse l'appelant décider du repli."""
    assert slugify("——— !!! ———") == ""
