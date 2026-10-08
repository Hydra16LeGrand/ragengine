"""Détection de langue : découpage en segments, pondération, et ses limites."""

from __future__ import annotations

import pytest

from app.ingestion.precheck.languages import (
    MIN_LANGUAGE_SHARE,
    SEGMENT_TARGET_CHARS,
    detect_languages,
    lexical_diversity,
    split_segments,
)

FRENCH = (
    "Le rapport annuel presente les resultats consolides de l'exercice ecoule. "
    "Le chiffre d'affaires progresse de douze pour cent sur l'ensemble des activites, "
    "porte par la croissance du secteur public territorial et des collectivites."
)

ENGLISH = (
    "The management board expects the coming financial year to remain stable. "
    "Revenue growth should continue at a slower pace across every business unit, "
    "while operating expenses are expected to increase moderately over the period."
)


TABLE_HEADER = (
    "Inventaire fictif complet, tableau a cheval sur deux pages. "
    "Le tableau ci-dessous compte trente-cinq lignes de donnees, volontairement trop long "
    "pour tenir sur une seule page : il se poursuit sur la suivante, avec repetition de "
    "la ligne d'en-tete, comportement standard de mise en page."
)

REPETITIVE_TABLE = "\n".join(
    [TABLE_HEADER, "Ref. fictive Designation Quantite Prix unitaire"]
    + [f"REF-{row:03d} Article fictif {row} {row % 20 + 1} {10 + row}.90" for row in range(1, 36)]
)
"""Corps de `17-pdf-table-spanning-pages.pdf` : « Article » est aussi un mot anglais."""

MIXED_SEGMENT = (
    "Fictive Corp - Rapport bilingue / Bilingual Report. "
    "This section is written in English. It describes, in a purely fictional way, the "
    "quarterly performance of the logistics division across three invented regions."
)
"""Segment reellement bilingue : `langdetect` y hesite autant que sur le tableau."""


def test_single_language_document() -> None:
    """Un document homogène ne renvoie qu'une langue."""
    assert detect_languages(FRENCH) == ["fr"]


def test_two_paragraphs_report_two_languages() -> None:
    """Chaque bloc est classé séparément : le multilinguisme reste visible."""
    assert set(detect_languages(f"{FRENCH}\n\n{ENGLISH}")) == {"fr", "en"}


def test_dominant_language_comes_first() -> None:
    """L'ordre du rapport traduit la couverture, pas l'ordre d'apparition."""
    text = f"{ENGLISH}\n\n{FRENCH}\n\n{FRENCH}\n\n{FRENCH}"

    assert detect_languages(text)[0] == "fr"


def test_marginal_language_is_discarded() -> None:
    """Une phrase isolée n'est pas une langue du document (seuil de couverture)."""
    text = "\n\n".join([FRENCH] * 12 + ["This is a short quotation."])

    assert detect_languages(text) == ["fr"]
    assert MIN_LANGUAGE_SHARE > 0


def test_repetitive_table_does_not_invent_a_second_language() -> None:
    """Régression réelle : un tableau français répétitif ressortait en `["fr", "en"]`.

    Le vocabulaire du corps se réduit à « Article fictif » ; `langdetect` y voit
    de l'anglais avec une confiance basse, et ce bruit pesait plein pot.
    """
    assert detect_languages(REPETITIVE_TABLE) == ["fr"]


def test_an_all_caps_legal_title_is_not_mistaken_for_german() -> None:
    """Régression réelle : le précheck d'un acte OHADA (`OHADA_15.11.2023.pdf`)
    rapportait ce document en anglais. Cause isolée : `langdetect` classe ce
    titre d'acte tout en capitales allemand à confiance 0,9999, français à la
    même confiance une fois mis en minuscules — un artefact du modèle sur les
    tout-capitales, courantes dans les titres de ce genre de document.
    """
    title = (
        "ACTE UNIFORME PORTANT ORGANISATION DES PROCÉDURES SIMPLIFIÉES DE "
        "RECOUVREMENT ET DES VOIES D'EXÉCUTION"
    )

    assert detect_languages(title) == ["fr"]


def test_an_uncertain_but_varied_segment_keeps_its_language() -> None:
    """Le garde-fou ne doit pas avaler une vraie seconde langue.

    Ce segment est classé avec une confiance aussi basse que le tableau
    ci-dessus — parce qu'il est *réellement* bilingue, pas parce qu'il est
    dégénéré. Sa richesse lexicale le sauve : c'est tout l'objet du `et`.
    """
    text = "\n\n".join([FRENCH, MIXED_SEGMENT, FRENCH])

    assert set(detect_languages(text)) == {"fr", "en"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("REF-001 Article fictif 1 REF-002 Article fictif 2 REF-003 Article fictif 3", 1 / 3),
        ("chaque mot de cette phrase apparait une seule fois", 1.0),
        ("1 2 3 -- 42 %", 1.0),
    ],
)
def test_lexical_diversity_measures_the_renewal_of_vocabulary(text: str, expected: float) -> None:
    """Nombres et codes sont hors sujet ; un segment sans mot vaut 1 (jamais écarté seul)."""
    assert lexical_diversity(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "   \n\n  ", "1 2 3 4 5 6", "42 %"])
def test_text_without_language_returns_an_empty_list(text: str) -> None:
    """L'absence de langue détectable est une information valide, pas une erreur."""
    assert detect_languages(text) == []


def test_blank_line_closes_a_segment() -> None:
    """Une ligne vide est le seul indice de bloc que laisse l'extraction PDF."""
    assert split_segments("premiere ligne\n\nseconde ligne") == ["premiere ligne", "seconde ligne"]


def test_long_block_is_cut_into_segments() -> None:
    """Un bloc continu est découpé : sinon une seule langue serait jamais renvoyée."""
    text = "\n".join(["une ligne de texte assez longue pour peser"] * 40)

    segments = split_segments(text)

    assert len(segments) > 1
    assert all(len(segment) < 2 * SEGMENT_TARGET_CHARS for segment in segments)
