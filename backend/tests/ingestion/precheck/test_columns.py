"""Détection des deux sections de texte côte à côte (ADR-042).

Ces tests portent sur le **verdict**, pas sur la géométrie intermédiaire : ce
que le reste de la chaîne consomme est un compteur de pages, et c'est lui qui
doit rester juste quand la mise en page change.

Le point délicat vérifié ici est la **discrimination tableau / colonnes**. Les
deux produisent des blancs verticaux ; seule la hauteur de la gouttière et le
nombre de bandes les séparent. Un faux positif enverrait un document à tableau
vers le mode qui ne sait pas reconnaître les tableaux — une régression, pas une
correction.
"""

from __future__ import annotations

import io

import pdfplumber
import pytest

from app.ingestion.precheck.columns import detect_gutters, page_has_two_columns
from app.ingestion.precheck.pdf import analyze_pdf
from tests.ingestion.precheck import documents


def _first_page(content: bytes) -> pdfplumber.page.Page:
    return pdfplumber.open(io.BytesIO(content)).pages[0]


def test_deux_colonnes_de_texte_sont_reconnues() -> None:
    """Le cas remonté : deux sections côte à côte, une seule gouttière."""
    page = _first_page(documents.two_column_pdf())

    assert page_has_two_columns(page) is True
    assert len(detect_gutters(page)) == 1


def test_un_bandeau_pleine_largeur_ne_masque_pas_la_gouttiere() -> None:
    """Un titre qui traverse la gouttière ne doit pas la nier.

    Presque tous les documents réels en portent un (titre, en-tête de section) :
    exiger une gouttière libre de haut en bas rendrait le détecteur inutile.
    """
    assert page_has_two_columns(_first_page(documents.two_column_pdf(banner=True))) is True
    assert page_has_two_columns(_first_page(documents.two_column_pdf(banner=False))) is True


def test_un_document_mono_colonne_ne_declenche_rien() -> None:
    """Le témoin : aucun blanc vertical traversant, donc aucune colonne."""
    page = _first_page(documents.text_pdf(pages=1))

    assert page_has_two_columns(page) is False
    assert detect_gutters(page) == []


@pytest.mark.parametrize("columns", [3, 4, 5])
def test_un_tableau_regle_n_est_pas_pris_pour_des_colonnes(columns: int) -> None:
    """Un tableau se présente en quatre ou cinq bandes, jamais en deux.

    C'est la restriction à **exactement deux bandes** qui l'écarte, et c'est la
    raison pour laquelle elle existe : rien dans la géométrie seule ne distingue
    une cellule d'un paragraphe.
    """
    page = _first_page(documents.table_pdf(rows=8, columns=columns))

    assert page_has_two_columns(page) is False


def test_une_page_quasi_vide_ne_declenche_rien() -> None:
    """Trop peu de mots pour que la géométrie veuille dire quoi que ce soit."""
    page = _first_page(documents.text_pdf(pages=1, paragraph=("Titre.",)))

    assert page_has_two_columns(page) is False


def test_le_compteur_du_precheck_suit_les_pages_a_colonnes() -> None:
    """Le rapport compte les pages, pas les documents : un document mixte le reste."""
    outcome = analyze_pdf(documents.two_column_pdf(pages=3))

    assert outcome.two_column_page_count == 3
    assert outcome.analyzed_page_count == 3


def test_un_document_sans_colonnes_rapporte_zero() -> None:
    """Valeur neutre explicite : l'interface doit pouvoir s'y fier sans test de nullité."""
    assert analyze_pdf(documents.text_pdf(pages=2)).two_column_page_count == 0


def test_un_scan_sans_couche_texte_ne_rapporte_aucune_colonne() -> None:
    """Sur un scan, il n'y a rien à mesurer — et c'est le bon verdict.

    Vérifié en ingestion réelle (ADR-042) : sous `Plain Text`, un PDF numérisé
    produit **0 fragment et 0 caractère**. Y proposer le mode direct au motif
    d'un ordre de lecture serait proposer de tout perdre ; le détecteur ne le
    fait pas, faute de mots à analyser.
    """
    assert analyze_pdf(documents.scanned_pdf()).two_column_page_count == 0
