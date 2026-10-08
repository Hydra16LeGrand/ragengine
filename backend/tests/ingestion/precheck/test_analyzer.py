"""Comportement du precheck sur de vrais documents fabriqués pour l'occasion.

Chaque test part d'un fichier réellement encodé (PDF produit par reportlab,
classeur produit par openpyxl), jamais d'un binaire simulé : c'est la seule
façon de vérifier que l'heuristique tient sur ce que produisent les outils
bureautiques.
"""

from __future__ import annotations

import pytest

from app.ingestion.precheck import (
    PrecheckErrorCode,
    PrecheckFormat,
    analyze_document,
    analyze_document_sync,
)
from app.ingestion.precheck import pdf as pdf_module
from app.ingestion.precheck.pdf import MAX_ANALYZED_PAGES, SCANNED_DOCUMENT_RATIO
from tests.ingestion.precheck import documents

PDF_MEDIA_TYPE = "application/pdf"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MEDIA_TYPE = "text/csv"


def test_text_pdf_is_not_reported_as_scanned() -> None:
    """Un PDF à couche texte : aucune numérisation, langue du corpus détectée."""
    report = analyze_document_sync(documents.text_pdf(pages=3), "rapport.pdf", PDF_MEDIA_TYPE)

    assert report.supported is True
    assert report.format is PrecheckFormat.PDF
    assert report.error is None
    assert report.page_count == 3
    assert report.analyzed_page_count == 3
    assert report.is_likely_scanned is False
    assert report.scanned_page_ratio == 0.0
    assert report.text_character_count > 0
    assert report.figure_count == 0
    assert report.detected_languages == ["fr"]


def test_image_only_pdf_is_reported_as_scanned() -> None:
    """Une page image sans couche texte : le cas que l'OCR lourd doit couvrir."""
    report = analyze_document_sync(documents.scanned_pdf(pages=2), "scan.pdf", PDF_MEDIA_TYPE)

    assert report.error is None
    assert report.is_likely_scanned is True
    assert report.scanned_page_ratio == 1.0
    assert report.text_character_count == 0
    # L'image de la page compte pour une figure : c'est documenté dans le schéma.
    assert report.figure_count == 2
    assert report.detected_languages == []


def test_scanned_pdf_with_retyped_header_is_still_reported_as_scanned() -> None:
    """Un en-tête ressaisi ne doit pas faire passer une numérisation pour du texte.

    C'est le cas que le seul comptage de caractères raterait : il est rattrapé
    par la couverture d'image de la page.
    """
    report = analyze_document_sync(
        documents.scanned_pdf(header="Direction generale des services - annexe 3"),
        "annexe.pdf",
        PDF_MEDIA_TYPE,
    )

    assert report.text_character_count > 0
    assert report.is_likely_scanned is True


@pytest.mark.parametrize(
    ("text_pages", "scanned_pages", "expected_ratio", "expected_verdict"),
    [
        (6, 4, 0.4, True),
        (9, 1, 0.1, False),
    ],
)
def test_mixed_pdf_verdict_follows_the_documented_ratio(
    text_pages: int, scanned_pages: int, expected_ratio: float, expected_verdict: bool
) -> None:
    """Le verdict suit `SCANNED_DOCUMENT_RATIO`, et le ratio brut reste affichable."""
    report = analyze_document_sync(
        documents.mixed_pdf(text_pages=text_pages, scanned_pages=scanned_pages),
        "mixte.pdf",
        PDF_MEDIA_TYPE,
    )

    assert report.scanned_page_ratio == pytest.approx(expected_ratio)
    assert report.is_likely_scanned is expected_verdict
    assert (expected_ratio >= SCANNED_DOCUMENT_RATIO) is expected_verdict


def test_illustrated_page_is_not_counted_as_a_scan() -> None:
    """Une page « titre court + schéma » n'est pas une page numérisée.

    Régression réelle du corpus (`05-pdf-mixed-text-table-diagram.pdf`) : sur
    deux pages, la seule page illustrée portait le ratio à 0,5 et la garde
    `force_deep_doc or is_likely_scanned` envoyait le document en DeepDOC sans
    que l'utilisateur puisse s'y opposer.
    """
    report = analyze_document_sync(documents.illustrated_pdf(), "schema.pdf", PDF_MEDIA_TYPE)

    assert report.page_count == 2
    assert report.figure_count == 1
    assert report.scanned_page_ratio == 0.0
    assert report.is_likely_scanned is False


def test_an_illustration_without_a_caption_stays_a_scan() -> None:
    """Sans légende, rien ne distingue une illustration d'un scan : le doute profite à l'OCR."""
    report = analyze_document_sync(
        documents.illustrated_pdf(caption=None), "schema-muet.pdf", PDF_MEDIA_TYPE
    )

    assert report.scanned_page_ratio == 0.5
    assert report.is_likely_scanned is True


def test_a_page_reduced_to_one_word_stays_a_scan() -> None:
    """Propriété de sécurité : une page sans texte exploitable n'échappe pas à DeepDOC.

    Le contre-exemple du plancher de pages analysées, écarté à ce titre : ce
    document d'une seule page cesserait d'être détecté.
    """
    report = analyze_document_sync(documents.single_word_pdf(), "mot.pdf", PDF_MEDIA_TYPE)

    assert report.page_count == 1
    assert report.figure_count == 0
    assert report.scanned_page_ratio == 1.0
    assert report.is_likely_scanned is True


def test_ruled_table_is_detected_in_a_pdf() -> None:
    """Un tableau réglé alimente l'option « Optimiser pour les tableaux »."""
    report = analyze_document_sync(documents.table_pdf(), "tableau.pdf", PDF_MEDIA_TYPE)

    assert report.table_count == 1
    assert report.is_likely_scanned is False


def test_bilingual_pdf_reports_both_languages() -> None:
    """Un paragraphe français suivi d'un paragraphe anglais : les deux ressortent."""
    report = analyze_document_sync(documents.bilingual_pdf(), "bilingue.pdf", PDF_MEDIA_TYPE)

    assert set(report.detected_languages) == {"fr", "en"}


def test_long_pdf_analysis_stays_bounded() -> None:
    """L'analyse d'un document volumineux reste bornée — un precheck doit être rapide."""
    report = analyze_document_sync(documents.text_pdf(pages=60), "long.pdf", PDF_MEDIA_TYPE)

    assert report.page_count == 60
    assert report.analyzed_page_count == MAX_ANALYZED_PAGES


def test_exhausted_time_budget_stops_the_analysis_without_failing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une page pathologique ne doit pas transformer le precheck en attente longue.

    Budget ramené à zéro : la première page est analysée quoi qu'il arrive, les
    suivantes sont abandonnées, et le rapport reste exploitable — il dit
    simplement combien de pages ont été vues.
    """
    monkeypatch.setattr(pdf_module, "PAGE_BUDGET_SECONDS", 0.0)

    report = analyze_document_sync(documents.text_pdf(pages=5), "lent.pdf", PDF_MEDIA_TYPE)

    assert report.error is None
    assert report.page_count == 5
    assert report.analyzed_page_count == 1
    assert report.detected_languages == ["fr"]


def test_workbook_reports_one_table_per_populated_sheet() -> None:
    """Un tableur est tabulaire par construction : une feuille non vide = un tableau."""
    content = documents.workbook_xlsx(
        {
            "Synthese": [
                ("Poste", "Montant"),
                ("Achats de fournitures administratives courantes", 1200),
                ("Charges de personnel affectees au service technique", 42000),
            ],
            "Detail": [("Mois", "Total"), ("janvier", 3), ("fevrier", 4)],
            "Feuille vide": [],
        }
    )

    report = analyze_document_sync(content, "budget.xlsx", XLSX_MEDIA_TYPE)

    assert report.supported is True
    assert report.format is PrecheckFormat.SPREADSHEET
    assert report.error is None
    assert report.table_count == 2
    assert report.page_count is None
    assert report.scanned_page_ratio is None
    assert report.is_likely_scanned is False
    assert report.detected_languages == ["fr"]


def test_csv_is_a_single_table() -> None:
    """Un CSV non vide compte pour un tableau, séparateur déduit du contenu."""
    content = documents.csv_bytes(
        [
            ("Poste", "Montant"),
            ("Achats de fournitures de bureau pour le service", "1200"),
            ("Charges de personnel affectees a la direction", "42000"),
        ]
    )

    report = analyze_document_sync(content, "export.csv", CSV_MEDIA_TYPE)

    assert report.format is PrecheckFormat.SPREADSHEET
    assert report.table_count == 1
    assert report.text_character_count > 0
    assert report.detected_languages == ["fr"]


def test_unsupported_format_produces_a_poor_report_not_an_exception() -> None:
    """Un format inconnu ne fait jamais échouer le precheck."""
    report = analyze_document_sync(b"PK\x03\x04...", "archive.zip", "application/zip")

    assert report.supported is False
    assert report.format is PrecheckFormat.UNSUPPORTED
    assert report.error is not None
    assert report.error.code is PrecheckErrorCode.UNSUPPORTED_FORMAT
    assert report.page_count is None
    assert report.table_count == 0
    assert report.detected_languages == []


def test_content_contradicting_the_extension_is_reported_as_corrupted() -> None:
    """Un `.pdf` sans en-tête PDF est signalé, pas analysé à l'aveugle."""
    report = analyze_document_sync(b"Ceci n'est pas un PDF.", "rapport.pdf", PDF_MEDIA_TYPE)

    assert report.supported is True
    assert report.format is PrecheckFormat.PDF
    assert report.error is not None
    assert report.error.code is PrecheckErrorCode.CORRUPTED_FILE


def test_empty_file_is_reported_as_corrupted() -> None:
    """Un téléversement vide produit un rapport explicite, jamais une exception."""
    report = analyze_document_sync(b"", "vide.pdf", PDF_MEDIA_TYPE)

    assert report.size_bytes == 0
    assert report.error is not None
    assert report.error.code is PrecheckErrorCode.CORRUPTED_FILE


def test_truncated_pdf_is_reported_as_extraction_failure() -> None:
    """Un PDF réel mais tronqué : l'échec d'extraction est capturé et journalisé."""
    complete = documents.text_pdf(pages=3)

    report = analyze_document_sync(complete[: len(complete) // 2], "tronque.pdf", PDF_MEDIA_TYPE)

    assert report.supported is True
    assert report.error is not None
    assert report.error.code is PrecheckErrorCode.EXTRACTION_FAILED
    assert report.page_count is None


def test_truncated_workbook_is_reported_as_extraction_failure() -> None:
    """Même contrat côté tableur : une archive ZIP amputée dégrade le rapport."""
    complete = documents.workbook_xlsx({"Synthese": [("Poste", "Montant"), ("Achats", 12)]})

    report = analyze_document_sync(complete[: len(complete) // 2], "budget.xlsx", XLSX_MEDIA_TYPE)

    assert report.error is not None
    assert report.error.code is PrecheckErrorCode.EXTRACTION_FAILED


@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    [
        ("sans-extension", PDF_MEDIA_TYPE, PrecheckFormat.PDF),
        ("rapport.PDF", "application/octet-stream", PrecheckFormat.PDF),
        ("classeur.xlsm", "application/octet-stream", PrecheckFormat.SPREADSHEET),
        ("export", "text/csv; charset=utf-8", PrecheckFormat.SPREADSHEET),
        ("photo.png", "image/png", PrecheckFormat.IMAGE),
        ("capture", "image/webp", PrecheckFormat.IMAGE),
        ("scan.TIFF", "application/octet-stream", PrecheckFormat.IMAGE),
        ("archive.xls", "application/vnd.ms-excel", PrecheckFormat.UNSUPPORTED),
        ("revue.pptx", "application/octet-stream", PrecheckFormat.PRESENTATION),
        ("comite.PPT", "application/octet-stream", PrecheckFormat.PRESENTATION),
        ("note.pages", "application/octet-stream", PrecheckFormat.PRESENTATION),
        ("diapos", "application/vnd.ms-powerpoint", PrecheckFormat.PRESENTATION),
    ],
)
def test_format_resolution_prefers_the_extension_then_the_media_type(
    filename: str, content_type: str, expected: PrecheckFormat
) -> None:
    """Le nom de fichier prime : un navigateur envoie souvent un type générique."""
    report = analyze_document_sync(documents.text_pdf(pages=1), filename, content_type)

    assert report.format is expected


def test_an_image_is_recognized_but_deliberately_not_analyzed() -> None:
    """Une image est reconnue et jugée traitable, sans qu'on prétende l'avoir lue.

    `supported = True` parce que la chaîne sait quoi en faire côté moteur ; les
    compteurs restent neutres parce que l'analyser demanderait un OCR local, que
    RAGEngine n'exécute jamais (CONTRIBUTING.md §2). `is_likely_scanned` est la seule
    exception : une image matricielle n'a, par construction, aucune couche texte.
    """
    report = analyze_document_sync(b"\x89PNG\r\n\x1a\n peu importe", "note.png", "image/png")

    assert report.supported is True
    assert report.format is PrecheckFormat.IMAGE
    assert report.error is None
    assert report.page_count is None
    assert report.analyzed_page_count == 0
    assert report.text_character_count == 0
    assert report.table_count == 0
    assert report.figure_count == 0
    assert report.detected_languages == []
    assert report.is_likely_scanned is True
    assert report.scanned_page_ratio is None


def test_a_presentation_is_recognized_but_deliberately_not_analyzed() -> None:
    """Un diaporama est reconnu pour son aiguillage, pas pour être décrit.

    Il ne suffit pas qu'il soit « traitable » : c'est cette famille de format
    qui permet à `chunking_profile.py` de **ne rien imposer** au moteur, seul
    profil que la garde `validate_chunk_method` accepte sur un `.pptx`. Le
    classer `UNSUPPORTED` le noyait avec `.docx`/`.md`/`.eml`, qui n'ont pas
    cette contrainte.

    Différence assumée avec l'image : `is_likely_scanned` reste `False` — un
    diaporama porte du texte natif, le dire numérisé serait faux à l'écran.
    """
    report = analyze_document_sync(b"PK\x03\x04 peu importe", "revue.pptx", "application/zip")

    assert report.supported is True
    assert report.format is PrecheckFormat.PRESENTATION
    assert report.error is None
    assert report.page_count is None
    assert report.analyzed_page_count == 0
    assert report.text_character_count == 0
    assert report.table_count == 0
    assert report.figure_count == 0
    assert report.two_column_page_count == 0
    assert report.detected_languages == []
    assert report.is_likely_scanned is False
    assert report.scanned_page_ratio is None


def test_an_empty_presentation_is_still_reported_as_corrupted() -> None:
    """Le format reconnu ne dispense pas du contrôle de contenu, ici non plus."""
    report = analyze_document_sync(b"", "revue.pptx", "application/octet-stream")

    assert report.format is PrecheckFormat.PRESENTATION
    assert report.error is not None
    assert report.error.code is PrecheckErrorCode.CORRUPTED_FILE


def test_an_empty_image_is_still_reported_as_corrupted() -> None:
    """Le format reconnu ne dispense pas du contrôle de contenu : zéro octet reste une anomalie."""
    report = analyze_document_sync(b"", "note.png", "image/png")

    assert report.format is PrecheckFormat.IMAGE
    assert report.error is not None
    assert report.error.code is PrecheckErrorCode.CORRUPTED_FILE


async def test_async_entry_point_matches_the_synchronous_one() -> None:
    """La façade FastAPI et les tâches Celery voient exactement le même rapport."""
    content = documents.text_pdf(pages=2)

    assert await analyze_document(content, "rapport.pdf", PDF_MEDIA_TYPE) == analyze_document_sync(
        content, "rapport.pdf", PDF_MEDIA_TYPE
    )


def test_report_serializes_to_the_jsonb_payload_of_the_document() -> None:
    """Le rapport part tel quel dans `documents.precheck_report` (JSONB)."""
    report = analyze_document_sync(documents.table_pdf(), "tableau.pdf", PDF_MEDIA_TYPE)

    payload = report.model_dump(mode="json")

    assert payload["format"] == "pdf"
    assert payload["error"] is None
    assert payload["detected_languages"] == report.detected_languages
    assert set(payload) == {
        "supported",
        "format",
        "size_bytes",
        "page_count",
        "analyzed_page_count",
        "text_character_count",
        "is_likely_scanned",
        "scanned_page_ratio",
        "table_count",
        "figure_count",
        "two_column_page_count",
        "detected_languages",
        "error",
    }
