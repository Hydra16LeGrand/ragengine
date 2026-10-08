"""Échantillonnage des pages : ce qui borne le coût du precheck."""

from __future__ import annotations

import pytest

from app.ingestion.precheck.pdf import (
    HEADER_FOOTER_MIN_PAGES,
    MAX_ANALYZED_PAGES,
    _strip_repeated_lines,
    sample_page_indexes,
)


@pytest.mark.parametrize("page_count", [0, 1, 5, MAX_ANALYZED_PAGES])
def test_short_documents_are_analyzed_entirely(page_count: int) -> None:
    """En deçà du plafond, aucune page n'est laissée de côté."""
    assert sample_page_indexes(page_count, MAX_ANALYZED_PAGES) == list(range(page_count))


@pytest.mark.parametrize("page_count", [MAX_ANALYZED_PAGES + 1, 60, 500, 5_000])
def test_long_documents_are_sampled_from_end_to_end(page_count: int) -> None:
    """L'échantillon couvre tout le document, extrémités comprises.

    Les annexes numérisées d'un rapport sont à la fin : un échantillon pris en
    tête les manquerait systématiquement.
    """
    indexes = sample_page_indexes(page_count, MAX_ANALYZED_PAGES)

    assert len(indexes) == MAX_ANALYZED_PAGES
    assert indexes == sorted(indexes)
    assert len(set(indexes)) == len(indexes)
    assert indexes[0] == 0
    assert indexes[-1] == page_count - 1


def test_strip_repeated_lines_removes_a_running_header_and_footer() -> None:
    """Régression réelle (`OHADA_15.11.2023.pdf`) : un en-tête d'export
    bureautique gravé sur chaque page (nom de fichier, horodatage, numéro de
    page) dominait le texte extrait au point de fausser la détection de
    langue en aval. Le numéro de page change à chaque occurrence : seule la
    normalisation par suites de chiffres permet de le reconnaître comme une
    seule et même ligne répétée. Le contenu du milieu, lui, n'est jamais
    candidat : c'est lui qui prouve que seuls les bords de page sont touchés.
    """
    pages = [
        f"Journal officiel OHADA 2023 08/11/2023 16:00 Page {n}\nContenu unique {n}\nPage {n} sur 4"
        for n in range(1, 5)
    ]

    stripped = _strip_repeated_lines(pages)

    assert stripped == [f"Contenu unique {n}" for n in range(1, 5)]


def test_strip_repeated_lines_never_touches_the_middle_of_a_page() -> None:
    """Un corps de page identique d'une page à l'autre (gabarit, mention légale
    reprise mot pour mot) n'est pas un en-tête. Régression réelle rencontrée en
    construisant ce correctif : un PDF de test dont les pages sont
    volontairement identiques perdait toute sa langue détectée, la totalité de
    son texte étant reconnue comme « répétée ». Seules la première et la
    dernière ligne sont candidates : jamais le milieu, quelle que soit sa
    répétition.
    """
    pages = ["Titre\nCorps identique\nCorps identique\nPied"] * HEADER_FOOTER_MIN_PAGES

    stripped = _strip_repeated_lines(pages)

    assert stripped == ["Corps identique\nCorps identique"] * HEADER_FOOTER_MIN_PAGES


def test_strip_repeated_lines_keeps_content_under_the_page_floor() -> None:
    """Trop peu de pages pour distinguer un en-tête répété d'un texte court par nature."""
    pages = ["Titre du document\nCorps"] * (HEADER_FOOTER_MIN_PAGES - 1)

    assert _strip_repeated_lines(pages) == pages


def test_strip_repeated_lines_keeps_a_minority_line() -> None:
    """Une ligne partagée par une minorité de pages est un vrai contenu, pas un en-tête."""
    pages = [
        "Ligne partagee\nContenu un",
        "Ligne partagee\nContenu deux",
        "Contenu trois",
        "Contenu quatre",
        "Contenu cinq",
        "Contenu six",
    ]

    assert _strip_repeated_lines(pages) == pages
