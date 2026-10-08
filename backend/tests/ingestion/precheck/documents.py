"""Fabrique de vrais fichiers de test pour le precheck.

Aucun binaire n'est commité : chaque fichier est reconstruit à l'exécution, ce
qui rend les tests lisibles (on voit ce que contient le document testé) et
reproductibles. Les PDF sont produits par `reportlab`, les classeurs par
`openpyxl` — deux dépendances de test, jamais importées par `app/`.

Les documents couvrent les cas que l'écran d'aiguillage doit savoir distinguer :
PDF texte, PDF numérisé (page image sans couche texte), PDF numérisé avec un
en-tête ressaisi, PDF à page illustrée (qui n'est *pas* une numérisation), PDF à
tableau réglé, PDF bilingue, classeur multi-feuilles, CSV, et fichiers
volontairement abîmés.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence

import openpyxl
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

PAGE_WIDTH, PAGE_HEIGHT = A4

FRENCH_PARAGRAPH: Sequence[str] = (
    "Le rapport annuel presente les resultats consolides de l'exercice ecoule.",
    "Le chiffre d'affaires progresse de douze pour cent sur l'ensemble des",
    "activites, porte par la croissance du secteur public territorial.",
    "Les charges d'exploitation restent maitrisees malgre la hausse des prix",
    "de l'energie, et la tresorerie disponible couvre largement les echeances",
    "de la dette bancaire contractee lors du precedent exercice comptable.",
)

ENGLISH_PARAGRAPH: Sequence[str] = (
    "The management board expects the coming financial year to remain stable.",
    "Revenue growth should continue at a slower pace across every business",
    "unit, while operating expenses are expected to increase moderately.",
    "The company keeps enough cash available to cover its banking commitments",
    "and intends to invest a significant share of its profits in research.",
    "No exceptional event occurred between the closing date and this report.",
)


def _draw_lines(canvas: Canvas, lines: Sequence[str], *, top: float = 780.0) -> float:
    """Écrit des lignes de texte et renvoie l'ordonnée atteinte."""
    canvas.setFont("Helvetica", 11)
    cursor = top
    for line in lines:
        canvas.drawString(60, cursor, line)
        cursor -= 18
    return cursor


def text_pdf(*, pages: int = 2, paragraph: Sequence[str] = FRENCH_PARAGRAPH) -> bytes:
    """PDF à couche texte : le cas nominal, `is_likely_scanned` doit rester faux."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    for _ in range(pages):
        _draw_lines(canvas, paragraph)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def two_column_pdf(*, pages: int = 1, banner: bool = True) -> bytes:
    """PDF à **deux sections de texte côte à côte** — le cas d'ADR-042.

    La gouttière est laissée à sa place naturelle (colonne gauche à x=60,
    colonne droite à x=320, sur une page A4 de 595 pt) et les deux colonnes
    descendent aussi bas l'une que l'autre : c'est cette hauteur commune qui
    distingue une mise en page en colonnes d'un espacement de tableau.

    `banner` ajoute un titre pleine largeur au-dessus des deux colonnes. Il ne
    doit **pas** empêcher la détection : une gouttière traversée par un seul
    bandeau reste une gouttière, sans quoi presque aucun document réel ne serait
    reconnu.
    """
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    for _ in range(pages):
        if banner:
            canvas.setFont("Helvetica-Bold", 14)
            canvas.drawString(60, 800, "Rapport annuel consolide de l'exercice ecoule")
        canvas.setFont("Helvetica", 11)
        cursor = 770.0
        for left, right in zip(FRENCH_PARAGRAPH, ENGLISH_PARAGRAPH, strict=False):
            canvas.drawString(60, cursor, left[:38])
            canvas.drawString(320, cursor, right[:38])
            cursor -= 18
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def bilingual_pdf() -> bytes:
    """PDF d'un paragraphe français suivi d'un paragraphe anglais."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    cursor = _draw_lines(canvas, FRENCH_PARAGRAPH)
    _draw_lines(canvas, ENGLISH_PARAGRAPH, top=cursor - 40)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def table_pdf(*, rows: int = 6, columns: int = 4) -> bytes:
    """PDF portant un vrai tableau réglé (traits horizontaux et verticaux)."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    _draw_lines(canvas, FRENCH_PARAGRAPH)
    origin_x, origin_y, width, height = 60.0, 400.0, 460.0, 220.0
    row_height = height / rows
    column_width = width / columns
    for index in range(rows + 1):
        canvas.line(
            origin_x, origin_y + index * row_height, origin_x + width, origin_y + index * row_height
        )
    for index in range(columns + 1):
        canvas.line(
            origin_x + index * column_width,
            origin_y,
            origin_x + index * column_width,
            origin_y + height,
        )
    canvas.setFont("Helvetica", 9)
    for row in range(rows):
        for column in range(columns):
            canvas.drawString(
                origin_x + column * column_width + 6,
                origin_y + row * row_height + 8,
                f"{row * columns + column}",
            )
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _scan_image() -> ImageReader:
    """Image d'une page numérisée : des barres noires en guise de lignes de texte."""
    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    for index in range(22):
        draw.rectangle((120, 180 + index * 62, 1120, 202 + index * 62), fill=(20, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def scanned_pdf(*, pages: int = 1, header: str | None = None) -> bytes:
    """PDF dont chaque page est une image pleine page, sans couche texte.

    `header` ressaisit une ligne de texte par-dessus l'image : c'est le cas que
    le seul comptage de caractères raterait, et que la couverture d'image
    rattrape.
    """
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    image = _scan_image()
    for _ in range(pages):
        canvas.drawImage(image, 0, 0, width=PAGE_WIDTH, height=PAGE_HEIGHT)
        if header is not None:
            canvas.setFont("Helvetica", 11)
            canvas.drawString(60, PAGE_HEIGHT - 40, header)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _diagram_image() -> ImageReader:
    """Schéma géométrique : un dessin, pas une numérisation de page."""
    image = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 50, 300, 200), outline=(0, 0, 0), width=4)
    draw.ellipse((400, 50, 650, 300), outline=(0, 0, 0), width=4)
    draw.line((100, 350, 800, 350), fill=(0, 0, 0), width=4)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def illustrated_pdf(*, caption: str | None = "Schema d'architecture (fictif)") -> bytes:
    """PDF « page de texte, puis page titre court + schéma » — pas un document numérisé.

    Reproduit `05-pdf-mixed-text-table-diagram.pdf` du corpus : le schéma couvre
    environ 17 % de la page, loin des 100 % d'une numérisation. `caption=None`
    retire la légende, cas où plus rien ne distingue la page d'un scan.
    """
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    _draw_lines(canvas, FRENCH_PARAGRAPH)
    canvas.showPage()
    if caption is not None:
        canvas.setFont("Helvetica", 11)
        canvas.drawString(60, PAGE_HEIGHT - 60, caption)
    canvas.drawImage(_diagram_image(), 60, 300, width=380, height=230)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def single_word_pdf(word: str = "Kangourou") -> bytes:
    """PDF d'un seul mot : aucune couche texte exploitable, aucune illustration."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    canvas.setFont("Helvetica", 11)
    canvas.drawString(60, PAGE_HEIGHT - 60, word)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def mixed_pdf(*, text_pages: int, scanned_pages: int) -> bytes:
    """PDF mêlant pages rédigées et annexes numérisées, dans cet ordre."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    for _ in range(text_pages):
        _draw_lines(canvas, FRENCH_PARAGRAPH)
        canvas.showPage()
    image = _scan_image()
    for _ in range(scanned_pages):
        canvas.drawImage(image, 0, 0, width=PAGE_WIDTH, height=PAGE_HEIGHT)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def workbook_xlsx(sheets: Mapping[str, Sequence[Sequence[object]]]) -> bytes:
    """Classeur `.xlsx` réel : une entrée par feuille, une ligne par séquence."""
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        worksheet = workbook.create_sheet(title=title)
        for row in rows:
            worksheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def csv_bytes(rows: Sequence[Sequence[str]], *, delimiter: str = ";") -> bytes:
    """CSV encodé en UTF-8, séparateur point-virgule par défaut (export Excel FR)."""
    lines = [delimiter.join(cell for cell in row) for row in rows]
    return "\n".join(lines).encode("utf-8")
