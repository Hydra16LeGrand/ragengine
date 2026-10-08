"""Vérification du contenu réel d'un téléversement par ses octets magiques (constat 9).

Chaque fixture est un contenu réel de son format (`PIL` pour les images,
`zipfile`/`openpyxl` pour les conteneurs OOXML), jamais un binaire simulé — même
principe que `tests/ingestion/precheck/test_analyzer.py`.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from PIL import Image

from app.core.errors import UnsupportedMediaTypeError
from app.services.file_signatures import ensure_content_matches_signature

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM_MEDIA_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.12"
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLS_MEDIA_TYPE = "application/vnd.ms-excel"
DOC_MEDIA_TYPE = "application/msword"
PPT_MEDIA_TYPE = "application/vnd.ms-powerpoint"

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"


def _ooxml_zip(top_level_dir: str) -> bytes:
    """Conteneur OOXML minimal mais réel : `[Content_Types].xml` + un dossier caractéristique."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(f"{top_level_dir}/dummy.xml", "<root/>")
    return buffer.getvalue()


def _ole2_bytes() -> bytes:
    return _OLE2_MAGIC + b"\x00" * 64


def _image_bytes(image_format: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buffer, format=image_format)
    return buffer.getvalue()


LEGITIMATE_CONTENTS = [
    pytest.param(PDF_MEDIA_TYPE, _pdf_bytes(), id="pdf"),
    pytest.param(DOCX_MEDIA_TYPE, _ooxml_zip("word"), id="docx"),
    pytest.param(XLSX_MEDIA_TYPE, _ooxml_zip("xl"), id="xlsx"),
    pytest.param(XLSM_MEDIA_TYPE, _ooxml_zip("xl"), id="xlsm"),
    pytest.param(PPTX_MEDIA_TYPE, _ooxml_zip("ppt"), id="pptx"),
    pytest.param(XLS_MEDIA_TYPE, _ole2_bytes(), id="xls-legacy"),
    pytest.param(DOC_MEDIA_TYPE, _ole2_bytes(), id="doc-legacy"),
    pytest.param(PPT_MEDIA_TYPE, _ole2_bytes(), id="ppt-legacy"),
    pytest.param("image/png", _image_bytes("PNG"), id="png"),
    pytest.param("image/jpeg", _image_bytes("JPEG"), id="jpeg"),
    pytest.param("image/tiff", _image_bytes("TIFF"), id="tiff"),
    pytest.param("image/gif", _image_bytes("GIF"), id="gif"),
    pytest.param("image/bmp", _image_bytes("BMP"), id="bmp"),
    pytest.param("image/webp", _image_bytes("WEBP"), id="webp"),
    pytest.param("text/csv", b"colonne_a;colonne_b\n1;2\n", id="csv"),
    pytest.param("application/csv", b"colonne_a,colonne_b\n1,2\n", id="csv-alt-media-type"),
    pytest.param("text/plain", "Un paragraphe en français, accents compris.".encode(), id="txt"),
    pytest.param("text/markdown", b"# Titre\n\nContenu.\n", id="md"),
    pytest.param("text/html", b"<html><body>Bonjour</body></html>", id="html"),
    pytest.param("application/json", b'{"cle": "valeur"}', id="json"),
    pytest.param("message/rfc822", b"From: a@example.com\nSubject: Test\n\nCorps.", id="eml"),
]


@pytest.mark.parametrize(("content_type", "content"), LEGITIMATE_CONTENTS)
def test_every_legitimate_content_type_is_accepted(content_type: str, content: bytes) -> None:
    """Aucun type réellement accepté par la liste blanche n'est refusé par erreur."""
    ensure_content_matches_signature(content, content_type)


def test_an_empty_file_is_accepted_for_a_text_format_without_magic_bytes() -> None:
    """Un texte brut n'a pas de signature binaire : vide reste un contenu légitime pour ce
    contrôle (d'autres garde-fous, hors périmètre ici, portent sur la taille/le quota)."""
    ensure_content_matches_signature(b"", "text/plain")


@pytest.mark.parametrize(
    "content_type",
    [PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE, XLSX_MEDIA_TYPE, XLS_MEDIA_TYPE, "image/png"],
)
def test_an_empty_file_is_refused_for_a_format_with_magic_bytes(content_type: str) -> None:
    """Vide : aucune signature à trouver, refusé plutôt que silencieusement accepté."""
    with pytest.raises(UnsupportedMediaTypeError):
        ensure_content_matches_signature(b"", content_type)


@pytest.mark.parametrize(
    ("content_type", "content"),
    [
        (PDF_MEDIA_TYPE, _pdf_bytes()[:2]),
        (DOCX_MEDIA_TYPE, _ooxml_zip("word")[:10]),
        (XLS_MEDIA_TYPE, _ole2_bytes()[:3]),
        ("image/png", _image_bytes("PNG")[:4]),
    ],
)
def test_a_truncated_file_is_refused(content_type: str, content: bytes) -> None:
    """Un fichier tronqué en cours de transfert n'est jamais confondu avec un fichier sain."""
    with pytest.raises(UnsupportedMediaTypeError):
        ensure_content_matches_signature(content, content_type)


def test_a_renamed_file_is_refused() -> None:
    """Le cas exact de l'audit : un contenu quelconque sous un nom en `.pdf`."""
    arbitrary_content = b"\x00\x01\x02\x03 ceci n'est pas un PDF " * 10

    with pytest.raises(UnsupportedMediaTypeError) as refused:
        ensure_content_matches_signature(arbitrary_content, PDF_MEDIA_TYPE)

    assert refused.value.extensions["content_type"] == PDF_MEDIA_TYPE


def test_a_pptx_disguised_as_a_docx_is_refused() -> None:
    """Le magic ZIP seul ne suffirait pas : les quatre formats OOXML le partagent."""
    pptx_content = _ooxml_zip("ppt")

    with pytest.raises(UnsupportedMediaTypeError):
        ensure_content_matches_signature(pptx_content, DOCX_MEDIA_TYPE)


def test_an_xlsx_disguised_as_a_pptx_is_refused() -> None:
    xlsx_content = _ooxml_zip("xl")

    with pytest.raises(UnsupportedMediaTypeError):
        ensure_content_matches_signature(xlsx_content, PPTX_MEDIA_TYPE)


def test_an_ordinary_archive_is_not_accepted_as_office_content() -> None:
    """Une archive ZIP quelconque (sans dossier OOXML caractéristique) n'est pas un document
    Office valide, même si elle a le bon magic ZIP."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "rien à voir avec un document Office")

    with pytest.raises(UnsupportedMediaTypeError):
        ensure_content_matches_signature(buffer.getvalue(), DOCX_MEDIA_TYPE)


def test_a_real_openpyxl_workbook_is_accepted() -> None:
    """Confirmation avec un classeur réellement produit par `openpyxl`, pas seulement un zip
    minimal fabriqué à la main."""
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "valeur"
    buffer = io.BytesIO()
    workbook.save(buffer)

    ensure_content_matches_signature(buffer.getvalue(), XLSX_MEDIA_TYPE)
