"""Analyse légère d'un tableur : `.xlsx`/`.xlsm` (openpyxl) et `.csv` (stdlib).

Un tableur est **tabulaire par construction** : il n'y a pas d'heuristique à
appliquer, seulement à compter ce qui porte réellement des données. Une feuille
non vide compte pour un tableau ; un CSV non vide compte pour un tableau. C'est
ce comptage qui motive l'option « Optimiser pour les tableaux » de l'écran
d'aiguillage (`docs/ingestion-engine-architecture.md` §4 → `chunk_method`
`table`).

**Bibliothèque : `openpyxl`** (MIT), ouverte en `read_only` : elle lit le
classeur en flux, sans charger toutes les cellules en mémoire. Le CSV n'a besoin
d'aucune dépendance, le module `csv` de la bibliothèque standard suffit.

Limites assumées :

- lecture bornée à `MAX_ANALYZED_SHEETS` feuilles et `MAX_ANALYZED_ROWS` lignes
  par feuille : une feuille dont les premières lignes sont vides mais qui porte
  des données plus bas est comptée comme vide ;
- `figure_count` reste à zéro : le mode `read_only` d'openpyxl n'expose pas les
  images, et les charger obligerait à lire tout le classeur — coût sans rapport
  avec le service rendu à l'écran d'aiguillage ;
- les anciens classeurs `.xls` (format binaire OLE, antérieur à 2007) ne sont
  pas pris en charge : ils produisent un rapport dégradé, pas une exception.

**Garde-fou bombe de décompression (audit sécurité 2026-09, constat 10, ADR-061).**
Un `.xlsx`/`.xlsm` est une archive ZIP : `openpyxl.load_workbook` décompresse
intégralement `sharedStrings.xml` et chaque feuille avant même que ce module
ne puisse borner quoi que ce soit. `_guard_against_zip_bomb` inspecte le
**répertoire central** de l'archive (`ZipFile.infolist()`, aucune entrée n'est
décompressée pour lire des tailles déclarées) et refuse le classeur avant
`load_workbook` si la taille décompressée totale ou le ratio d'une entrée
dépasse les seuils configurés (`Settings.spreadsheet_max_decompressed_bytes`,
`spreadsheet_max_decompression_ratio`) — jamais après avoir commencé à écrire
des gigaoctets en mémoire.

**Autres conteneurs ZIP du même pipeline : pas concernés.** `.docx`/`.pptx`
sont reconnus par le precheck pour leur seul aiguillage
(`app.ingestion.precheck.analyzer._Source.PRESENTATION`/l'absence
d'extracteur pour DOCX) : aucun des deux n'est jamais ouvert par ce module ni
par un autre — seul RAGFlow les traite, dans son propre conteneur, hors
périmètre de RAGEngine (CONTRIBUTING.md §3). Rien d'autre à garder ici.
"""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Final

import openpyxl

from app.core.config import Settings, get_settings
from app.ingestion.precheck.extraction import ExtractionError, ExtractionOutcome

ZIP_MAGIC: Final = b"PK\x03\x04"
"""Un `.xlsx` est une archive ZIP : son en-tête le distingue d'un `.xls` binaire."""

MAX_ANALYZED_SHEETS: Final = 50
MAX_ANALYZED_ROWS: Final = 200
MAX_TEXT_SAMPLE_CHARS: Final = 20_000
"""Plafond de texte conservé pour la détection de langue — jamais persisté."""

CSV_SNIFF_BYTES: Final = 8_192
CSV_DELIMITERS: Final = ",;\t|"
"""Les exports francophones d'Excel utilisent `;`, pas `,`."""

_MIN_RATIO_CHECK_BYTES: Final = 1024 * 1024
"""Sous ce volume décompressé, le ratio d'une entrée est ignoré : une petite
entrée interne du conteneur (`_rels/.rels`, `[Content_Types].xml`…) peut
afficher un ratio élevé sans le moindre risque — la taille absolue en jeu est
négligeable, et l'utilisateur n'a de toute façon aucune prise sur ces entrées."""


def looks_like_xlsx(content: bytes) -> bool:
    """Vérifie l'en-tête ZIP : un `.xlsx` qui n'en a pas est corrompu (ou un vieux `.xls`)."""
    return content.startswith(ZIP_MAGIC)


def _guard_against_zip_bomb(content: bytes, settings: Settings) -> None:
    """Refuse un classeur dont la décompression menacerait la mémoire du worker.

    Ne lit que le répertoire central de l'archive (`ZipFile.infolist()`) :
    aucune entrée n'est décompressée ici, seules les tailles que le format ZIP
    déclare lui-même sont additionnées et comparées. Un fichier dont l'annonce
    serait mensongère reste, par construction du format, détecté au premier
    écart par `openpyxl.load_workbook` lui-même (`BadZipFile` ou équivalent),
    couvert par le `except Exception` de l'appelant.

    Un zip invalide n'est **pas** signalé ici : c'est `openpyxl.load_workbook`
    qui produira la même `ExtractionError` que pour tout autre classeur
    corrompu, sans dupliquer ce diagnostic.

    Raises:
        ExtractionError: taille décompressée totale, ou ratio d'une entrée
            (au-delà de `_MIN_RATIO_CHECK_BYTES`), au-delà des seuils
            configurés — bombe de décompression suspectée.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile:
        return

    total_uncompressed = 0
    for info in infos:
        total_uncompressed += info.file_size
        if info.file_size < _MIN_RATIO_CHECK_BYTES:
            continue
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > settings.spreadsheet_max_decompression_ratio:
            raise ExtractionError(
                "Classeur refusé : une entrée interne se décompresse à un ratio "
                f"anormal (~{ratio:.0f}:1, plafond {settings.spreadsheet_max_decompression_ratio}"
                ":1) — bombe de décompression suspectée."
            )
    if total_uncompressed > settings.spreadsheet_max_decompressed_bytes:
        raise ExtractionError(
            "Classeur refusé : taille décompressée totale "
            f"({total_uncompressed} octets) au-delà du plafond autorisé "
            f"({settings.spreadsheet_max_decompressed_bytes} octets) — bombe de "
            "décompression suspectée."
        )


def _cell_text(value: object) -> str:
    """Ne retient que le texte saisi : un nombre ou une date n'a pas de langue."""
    return value if isinstance(value, str) else ""


def analyze_xlsx(content: bytes, settings: Settings | None = None) -> ExtractionOutcome:
    """Compte les feuilles porteuses de données et collecte leur texte.

    `settings` est résolu via `get_settings()` si non fourni — comme dans
    `app.workers.tasks.*`, ce module tourne aussi bien depuis une tâche Celery
    synchrone que depuis un thread de la façade (`analyzer.analyze_document`),
    aucune des deux ne portant d'injection de dépendances FastAPI ici.
    """
    resolved_settings = settings if settings is not None else get_settings()
    _guard_against_zip_bomb(content, resolved_settings)
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise ExtractionError(f"{type(exc).__name__}: {exc}") from exc

    tables = 0
    characters = 0
    texts: list[str] = []
    sample_length = 0
    try:
        for worksheet in workbook.worksheets[:MAX_ANALYZED_SHEETS]:
            has_data = False
            for row in worksheet.iter_rows(max_row=MAX_ANALYZED_ROWS, values_only=True):
                if any(value is not None for value in row):
                    has_data = True
                line = " ".join(cell for cell in (_cell_text(value) for value in row) if cell)
                if line:
                    characters += sum(1 for character in line if not character.isspace())
                    if sample_length < MAX_TEXT_SAMPLE_CHARS:
                        texts.append(line)
                        sample_length += len(line)
            if has_data:
                tables += 1
            # Sépare les feuilles pour la segmentation de la détection de langue.
            texts.append("")
    except Exception as exc:
        raise ExtractionError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        workbook.close()

    return _spreadsheet_outcome(tables=tables, characters=characters, texts=texts)


def _decode(content: bytes) -> str:
    """Décode un CSV sans jamais échouer : UTF-8, puis CP1252 (export Excel Windows)."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def analyze_csv(content: bytes) -> ExtractionOutcome:
    """Lit les premières lignes d'un CSV, séparateur déduit du contenu."""
    text = _decode(content)
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            text[:CSV_SNIFF_BYTES], delimiters=CSV_DELIMITERS
        )
    except csv.Error:
        # Fichier à une seule colonne, ou séparateur exotique : le dialecte par
        # défaut lit alors chaque ligne comme une cellule unique, ce qui reste
        # un tableau à une colonne.
        dialect = csv.excel

    characters = 0
    texts: list[str] = []
    sample_length = 0
    rows = 0
    try:
        for row in csv.reader(io.StringIO(text), dialect):
            if rows >= MAX_ANALYZED_ROWS:
                break
            rows += 1
            line = " ".join(cell for cell in row if cell)
            if line:
                characters += sum(1 for character in line if not character.isspace())
                if sample_length < MAX_TEXT_SAMPLE_CHARS:
                    texts.append(line)
                    sample_length += len(line)
    except csv.Error as exc:
        raise ExtractionError(f"{type(exc).__name__}: {exc}") from exc

    return _spreadsheet_outcome(tables=1 if rows else 0, characters=characters, texts=texts)


def _spreadsheet_outcome(*, tables: int, characters: int, texts: list[str]) -> ExtractionOutcome:
    """Assemble le résultat commun aux tableurs : pas de pagination, pas de scan."""
    return ExtractionOutcome(
        page_count=None,
        analyzed_page_count=0,
        text_character_count=characters,
        is_likely_scanned=False,
        scanned_page_ratio=None,
        table_count=tables,
        figure_count=0,
        # Un tableur n'a pas de mise en page : la notion de colonnes côte à côte
        # n'y a pas de sens, et son aiguillage ne passe de toute façon jamais par
        # l'analyse de mise en page (`chunking_profile.py`, chemin `table`).
        two_column_page_count=0,
        text_sample="\n".join(texts)[:MAX_TEXT_SAMPLE_CHARS],
    )
