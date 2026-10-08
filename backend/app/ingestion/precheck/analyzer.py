"""Analyse préalable d'un document — point d'entrée du precheck (Schéma A).

`analyze_document` répond à la seule question que l'écran d'aiguillage pose à
l'utilisateur : *à quoi ressemble ce document ?* — combien de pages, du texte
extractible ou une numérisation, des tableaux, des schémas, quelles langues.
L'utilisateur valide ou ajuste, et c'est **sa** décision qui détermine ensuite
la stratégie de découpage envoyée au moteur d'ingestion
(`docs/ingestion-engine-architecture.md` §4).

**Pourquoi RAGEngine fait cette analyse lui-même.** RAGFlow n'analyse un
document qu'*après* qu'on lui ait imposé une méthode de découpage : il n'existe
aucun moyen de l'interroger avant de s'engager. Le precheck est donc délibérément
indépendant du moteur, et le reste : ni appel réseau, ni service d'inférence, ni
GPU (CONTRIBUTING.md §2). Tout est local, CPU, et borné pour tenir en quelques
secondes — la vraie extraction visuelle, elle, appartient à RAGFlow, après la
décision.

**Le precheck n'échoue jamais.** Format inconnu, fichier tronqué, PDF chiffré :
chaque cas produit un rapport dégradé (`supported = False` et/ou `error`
renseigné), jamais une exception qui remonterait à l'appelant. Un signal d'aide
à la décision qui ferait échouer une ingestion serait pire que pas de signal.

Deux points d'entrée, même travail :

- `analyze_document` (async) pour la façade FastAPI — le travail, purement CPU,
  est déporté dans un thread pour ne pas bloquer la boucle d'événements ;
- `analyze_document_sync` pour les tâches Celery, qui sont synchrones.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final

import anyio.to_thread

from app.ingestion.precheck.extraction import ExtractionError, ExtractionOutcome
from app.ingestion.precheck.languages import detect_languages
from app.ingestion.precheck.pdf import analyze_pdf, looks_like_pdf
from app.ingestion.precheck.schemas import (
    PrecheckError,
    PrecheckErrorCode,
    PrecheckFormat,
    PrecheckReport,
)
from app.ingestion.precheck.spreadsheet import analyze_csv, analyze_xlsx, looks_like_xlsx

_logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_CHARS: Final = 300
"""Un message d'erreur est affiché à l'utilisateur : il reste court et lisible."""


class _Source(StrEnum):
    """Format concret reconnu, plus fin que `PrecheckFormat` (extracteur à appeler)."""

    PDF = "pdf"
    XLSX = "xlsx"
    CSV = "csv"
    IMAGE = "image"
    PRESENTATION = "presentation"
    UNSUPPORTED = "unsupported"


_EXTENSIONS: Final[dict[str, _Source]] = {
    ".pdf": _Source.PDF,
    ".xlsx": _Source.XLSX,
    # Classeur à macros : même conteneur OOXML, openpyxl le lit sans réserve.
    ".xlsm": _Source.XLSX,
    ".csv": _Source.CSV,
    # Images seules : le miroir exact des types acceptés à l'upload
    # (`Settings.upload_allowed_content_types`). Toute extension listée ici doit
    # y avoir son type de média, sinon le fichier serait refusé avant d'arriver.
    # Le miroir est volontairement tenu sur *notre* liste d'admission, pas sur ce
    # que le moteur sait finalement traiter : `.bmp` et `.tiff` (mais pas `.tif`)
    # sont acceptés ici puis refusés par le moteur au téléversement (« This type
    # of file has not been supported yet! », fait vérifié). Les classer
    # `UNSUPPORTED` ne les sauverait pas — le rapport de precheck ne décide de
    # rien — et masquerait le vrai écart, qui est dans la liste d'admission.
    ".png": _Source.IMAGE,
    ".jpg": _Source.IMAGE,
    ".jpeg": _Source.IMAGE,
    ".tif": _Source.IMAGE,
    ".tiff": _Source.IMAGE,
    ".gif": _Source.IMAGE,
    ".bmp": _Source.IMAGE,
    ".webp": _Source.IMAGE,
    # Diaporamas : **exactement** les trois extensions de la garde du moteur
    # (`validate_chunk_method`, `FileService.get_parser`), pas une liste maison.
    # Les reconnaître ici est ce qui permet à `chunking_profile.py` de ne rien
    # imposer au moteur sur ce format — cf. `PrecheckFormat.PRESENTATION`.
    # `.pages` n'a pas de type de média admis à l'upload : il est listé quand
    # même pour que l'ajouter un jour ne fasse pas ressurgir le rejet moteur.
    ".pptx": _Source.PRESENTATION,
    ".ppt": _Source.PRESENTATION,
    ".pages": _Source.PRESENTATION,
}

_CONTENT_TYPES: Final[dict[str, _Source]] = {
    "application/pdf": _Source.PDF,
    "application/x-pdf": _Source.PDF,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _Source.XLSX,
    "application/vnd.ms-excel.sheet.macroenabled.12": _Source.XLSX,
    "text/csv": _Source.CSV,
    "application/csv": _Source.CSV,
    "image/png": _Source.IMAGE,
    "image/jpeg": _Source.IMAGE,
    "image/tiff": _Source.IMAGE,
    "image/gif": _Source.IMAGE,
    "image/bmp": _Source.IMAGE,
    "image/webp": _Source.IMAGE,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        _Source.PRESENTATION
    ),
    "application/vnd.ms-powerpoint": _Source.PRESENTATION,
}

_PUBLIC_FORMATS: Final[dict[_Source, PrecheckFormat]] = {
    _Source.PDF: PrecheckFormat.PDF,
    _Source.XLSX: PrecheckFormat.SPREADSHEET,
    _Source.CSV: PrecheckFormat.SPREADSHEET,
    _Source.IMAGE: PrecheckFormat.IMAGE,
    _Source.PRESENTATION: PrecheckFormat.PRESENTATION,
    _Source.UNSUPPORTED: PrecheckFormat.UNSUPPORTED,
}

_EXTRACTORS: Final[dict[_Source, Callable[[bytes], ExtractionOutcome]]] = {
    _Source.PDF: analyze_pdf,
    _Source.XLSX: analyze_xlsx,
    _Source.CSV: analyze_csv,
}
"""Extracteurs par format. `_Source.IMAGE` n'y figure **pas**, délibérément :
lire le contenu d'une image demanderait un OCR local, que RAGEngine n'exécute
jamais (CONTRIBUTING.md §2 — l'extraction visuelle appartient au moteur, après la
décision). `_Source.PRESENTATION` non plus : le format est reconnu pour son
**aiguillage** (ne rien imposer au moteur, cf. `PrecheckFormat.PRESENTATION`),
pas pour être décrit — ouvrir un `.pptx` ici ajouterait une dépendance de
parsing pour un rapport dont aucun champ ne changerait de décision. Un format
sans extracteur produit un rapport minimal, cf. `_unanalyzed`."""

_MAGIC_CHECKS: Final[dict[_Source, Callable[[bytes], bool]]] = {
    _Source.PDF: looks_like_pdf,
    _Source.XLSX: looks_like_xlsx,
}


def resolve_source(filename: str, content_type: str) -> _Source:
    """Détermine le format à analyser depuis l'extension, puis le type de média.

    L'extension prime : un navigateur envoie régulièrement
    `application/octet-stream` pour un fichier parfaitement identifiable par son
    nom, alors que l'inverse — une extension absente et un type de média juste —
    est plus rare.
    """
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in _EXTENSIONS:
        return _EXTENSIONS[suffix]
    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    return _CONTENT_TYPES.get(media_type, _Source.UNSUPPORTED)


def _degraded(
    *,
    source: _Source,
    size_bytes: int,
    code: PrecheckErrorCode,
    message: str,
) -> PrecheckReport:
    """Rapport pauvre mais valide : l'ingestion peut continuer, sans recommandation."""
    return PrecheckReport(
        supported=source is not _Source.UNSUPPORTED,
        format=_PUBLIC_FORMATS[source],
        size_bytes=size_bytes,
        error=PrecheckError(code=code, message=message[:MAX_ERROR_MESSAGE_CHARS]),
    )


def _unanalyzed(*, source: _Source, size_bytes: int) -> PrecheckReport:
    """Rapport minimal d'un format reconnu mais volontairement non inspecté.

    Ni dégradé ni faux : `supported = True` parce que la chaîne **sait** traiter
    ce document (le format arbitre son `chunk_method`,
    `docs/ingestion-engine-architecture.md` §4) ; les compteurs restent neutres
    parce que rien n'a été lu, et `error` reste `None` parce qu'il ne s'est rien
    passé d'anormal.

    Seule exception aux valeurs neutres : `is_likely_scanned`, et il **dépend du
    format**. Ce n'est pas une heuristique ici mais une propriété de chacun —
    une image matricielle ne porte, par construction, aucune couche texte, là où
    un diaporama est nativement textuel. Dire `True` d'un `.pptx` ferait afficher
    « document numérisé » sur l'écran d'aiguillage à propos d'un fichier qui ne
    l'est pas.

    Ce `True` ne se traduit **pas** par une proposition sur l'écran
    d'aiguillage : les deux options sont des no-op documentés sur une image
    (`app/ingestion/chunking_profile.py`, seul le profil vide est accepté par le
    moteur, qui OCRise déjà au maximum), et l'interface ne propose rien qui ne
    ferait rien (ADR-031). Il reste juste comme **fait rapporté**.
    """
    return PrecheckReport(
        supported=True,
        format=_PUBLIC_FORMATS[source],
        size_bytes=size_bytes,
        page_count=None,
        analyzed_page_count=0,
        is_likely_scanned=source is _Source.IMAGE,
        scanned_page_ratio=None,
        error=None,
    )


def analyze_document_sync(content: bytes, filename: str, content_type: str) -> PrecheckReport:
    """Analyse un document et produit son rapport de precheck (version synchrone).

    Ne lève jamais : toute anomalie devient un rapport dégradé.

    :param content: contenu binaire intégral du fichier téléversé.
    :param filename: nom d'origine, seule source fiable du format en pratique.
    :param content_type: type de média annoncé par le client, utilisé en second.
    """
    size_bytes = len(content)
    source = resolve_source(filename, content_type)

    if source is _Source.UNSUPPORTED:
        return _degraded(
            source=source,
            size_bytes=size_bytes,
            code=PrecheckErrorCode.UNSUPPORTED_FORMAT,
            message=(
                "Ce format n'est pas analysé en amont de l'ingestion : le document "
                "sera traité sans recommandation préalable."
            ),
        )

    if not content:
        return _degraded(
            source=source,
            size_bytes=size_bytes,
            code=PrecheckErrorCode.CORRUPTED_FILE,
            message="Le fichier est vide.",
        )

    magic_check = _MAGIC_CHECKS.get(source)
    if magic_check is not None and not magic_check(content):
        return _degraded(
            source=source,
            size_bytes=size_bytes,
            code=PrecheckErrorCode.CORRUPTED_FILE,
            message=(
                "Le contenu du fichier ne correspond pas à son extension : "
                "il est probablement endommagé ou mal nommé."
            ),
        )

    extractor = _EXTRACTORS.get(source)
    if extractor is None:
        # Format reconnu, volontairement non inspecté (image, diaporama) :
        # rapport minimal plutôt que dégradé — l'ingestion sait quoi en faire.
        return _unanalyzed(source=source, size_bytes=size_bytes)

    try:
        outcome = extractor(content)
    except ExtractionError as exc:
        # `filename` est un attribut réservé de `LogRecord` : le passer en
        # `extra` ferait lever le logger lui-même. D'où le préfixe `document_`.
        _logger.warning(
            "Precheck dégradé : extraction impossible",
            extra={"document_filename": filename, "document_format": source.value},
            exc_info=True,
        )
        return _degraded(
            source=source,
            size_bytes=size_bytes,
            code=PrecheckErrorCode.EXTRACTION_FAILED,
            message=f"Le contenu n'a pas pu être extrait ({exc}).",
        )
    except Exception:
        # Filet de sécurité : une bibliothèque tierce peut lever n'importe quoi
        # sur un fichier malformé, et le precheck ne doit jamais faire tomber
        # l'ingestion pour autant.
        _logger.exception(
            "Precheck dégradé : erreur inattendue pendant l'analyse",
            extra={"document_filename": filename, "document_format": source.value},
        )
        return _degraded(
            source=source,
            size_bytes=size_bytes,
            code=PrecheckErrorCode.EXTRACTION_FAILED,
            message="Le contenu n'a pas pu être analysé.",
        )

    return PrecheckReport(
        supported=True,
        format=_PUBLIC_FORMATS[source],
        size_bytes=size_bytes,
        page_count=outcome.page_count,
        analyzed_page_count=outcome.analyzed_page_count,
        text_character_count=outcome.text_character_count,
        is_likely_scanned=outcome.is_likely_scanned,
        scanned_page_ratio=outcome.scanned_page_ratio,
        table_count=outcome.table_count,
        figure_count=outcome.figure_count,
        two_column_page_count=outcome.two_column_page_count,
        detected_languages=detect_languages(outcome.text_sample),
        error=None,
    )


async def analyze_document(content: bytes, filename: str, content_type: str) -> PrecheckReport:
    """Analyse un document sans bloquer la boucle d'événements.

    Le travail est entièrement CPU (parsing PDF, lecture de classeur, n-grammes
    de langue) : il est exécuté dans un thread, comme toute opération bloquante
    appelée depuis FastAPI (CONTRIBUTING.md §6).
    """
    return await anyio.to_thread.run_sync(analyze_document_sync, content, filename, content_type)
