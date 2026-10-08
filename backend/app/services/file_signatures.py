"""Vérification du contenu réel d'un téléversement, par ses octets magiques.

**Pourquoi** (audit sécurité 2026-09, constat 9). `resolve_content_type`
(`app.services.documents`) s'appuie sur l'extension du nom de fichier
(`mimetypes.guess_type`), puis, à défaut, sur le type déclaré par le client —
jamais sur le contenu. `ensure_upload_within_limits` compare ce résultat à une
liste blanche mais ne regarde, elle non plus, aucun octet : un fichier
quelconque renommé en `.pdf` la traversait donc sans être détecté.
`ensure_content_matches_signature` referme ce trou, appelée juste après (même
emplacement que la liste blanche, avant l'empreinte SHA-256 et toute écriture,
`DocumentService.register`).

**Trois familles de contrôle**, une par forme de conteneur :

- **Signature fixe en tête de fichier** (PDF, images) : comparaison directe des
  premiers octets, comme `app.ingestion.precheck.pdf.looks_like_pdf` — réutilisée
  ici plutôt que redupliquée.
- **Conteneur ZIP** (DOCX/XLSX/XLSM/PPTX) : le magic ZIP seul (`PK\\x03\\x04`,
  cf. `app.ingestion.precheck.spreadsheet.looks_like_xlsx`) ne distingue pas les
  quatre formats OOXML, qui le partagent tous — un `.pptx` renommé en `.docx`
  le passerait. On ouvre l'archive et on lit son **répertoire central**
  (`ZipFile.namelist()`, aucune entrée n'est décompressée pour lister des noms —
  pas le chemin visé par la bombe de décompression du constat 10, gardée
  séparément dans `app.ingestion.precheck.spreadsheet`) pour vérifier la
  présence de `[Content_Types].xml` (commun aux quatre) et du dossier
  caractéristique du format (`word/`, `xl/`, `ppt/`).
- **Conteneur OLE2/CFBF** (DOC/XLS/PPT, format binaire pré-2007) : les trois
  partagent le même en-tête de 8 octets. Les distinguer précisément
  demanderait de parser le répertoire interne du conteneur (bibliothèque type
  `olefile`, absente de la bibliothèque standard) — non justifié ici : la
  menace visée est un fichier quelconque déguisé en document Office, pas la
  confusion entre deux formats Office legacy eux-mêmes acceptés l'un comme
  l'autre à l'admission. Le contrôle CFBF suffit à bloquer le premier cas.

**Formats sans octets magiques** (texte brut : CSV, TXT, MD, HTML, JSON,
`message/rfc822`) : acceptés explicitement, sans contrôle de contenu — un
fichier texte quelconque n'a pas de signature binaire à vérifier, et il est de
toute façon indexé tel quel, jamais exécuté ni interprété.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from io import BytesIO
from typing import Final

from app.core.errors import UnsupportedMediaTypeError
from app.ingestion.precheck.pdf import looks_like_pdf

_OLE2_MAGIC: Final = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
"""En-tête « Compound File Binary Format » : Office pré-2007 (`.doc`, `.xls`, `.ppt`)."""

_ZIP_LOCAL_FILE_MAGIC: Final = b"PK\x03\x04"
_ZIP_EMPTY_ARCHIVE_MAGIC: Final = b"PK\x05\x06"

_PNG_MAGIC: Final = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC: Final = b"\xff\xd8\xff"
_GIF_MAGICS: Final = (b"GIF87a", b"GIF89a")
_BMP_MAGIC: Final = b"BM"
_TIFF_MAGICS: Final = (b"II*\x00", b"MM\x00*")
"""TIFF : petit-boutiste (`II`) ou grand-boutiste (`MM`), les deux légitimes."""

_OOXML_CONTENT_TYPES_ENTRY: Final = "[Content_Types].xml"


def _looks_like_zip(content: bytes) -> bool:
    return content.startswith(_ZIP_LOCAL_FILE_MAGIC) or content.startswith(_ZIP_EMPTY_ARCHIVE_MAGIC)


def _zip_contains(content: bytes, *, top_level_prefix: str) -> bool:
    """Ouvre l'archive (répertoire central seulement) et cherche un dossier caractéristique.

    Ne décompresse aucune entrée : `ZipFile.namelist()` ne lit que les noms du
    répertoire central. Un fichier tronqué ou corrompu lève `BadZipFile`,
    traité ici comme une signature absente plutôt que comme une erreur —
    cohérent avec le reste du module, qui ne fait jamais remonter d'exception
    tierce à l'appelant.
    """
    if not _looks_like_zip(content):
        return False
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return False
    return _OOXML_CONTENT_TYPES_ENTRY in names and any(
        name.startswith(top_level_prefix) for name in names
    )


def _looks_like_docx(content: bytes) -> bool:
    return _zip_contains(content, top_level_prefix="word/")


def _looks_like_ooxml_spreadsheet(content: bytes) -> bool:
    """`.xlsx` **et** `.xlsm` : le classeur à macros est un conteneur OOXML comme l'autre,
    malgré son type de média hérité de la famille `application/vnd.ms-excel` (§ config)."""
    return _zip_contains(content, top_level_prefix="xl/")


def _looks_like_pptx(content: bytes) -> bool:
    return _zip_contains(content, top_level_prefix="ppt/")


def _looks_like_ole2(content: bytes) -> bool:
    return content.startswith(_OLE2_MAGIC)


def _looks_like_png(content: bytes) -> bool:
    return content.startswith(_PNG_MAGIC)


def _looks_like_jpeg(content: bytes) -> bool:
    return content.startswith(_JPEG_MAGIC)


def _looks_like_gif(content: bytes) -> bool:
    return content.startswith(_GIF_MAGICS)


def _looks_like_bmp(content: bytes) -> bool:
    return content.startswith(_BMP_MAGIC)


def _looks_like_tiff(content: bytes) -> bool:
    return content.startswith(_TIFF_MAGICS)


def _looks_like_webp(content: bytes) -> bool:
    return content[:4] == b"RIFF" and content[8:12] == b"WEBP"


_SIGNATURE_CHECKS: Final[dict[str, Callable[[bytes], bool] | None]] = {
    "application/pdf": looks_like_pdf,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _looks_like_docx,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        _looks_like_ooxml_spreadsheet
    ),
    # `.xlsm` officiel (RFC IANA/Microsoft) : `macroEnabled` en casse mixte —
    # `mimetypes.guess_type` peut renvoyer l'une ou l'autre selon les fichiers
    # système de la plateforme (constaté : casse mixte sur cette machine,
    # config `upload_allowed_content_types` en minuscules). Les deux clés
    # pointent le même vérificateur, défensif quelle que soit la casse qui
    # aura franchi la liste blanche.
    "application/vnd.ms-excel.sheet.macroEnabled.12": _looks_like_ooxml_spreadsheet,
    "application/vnd.ms-excel.sheet.macroenabled.12": _looks_like_ooxml_spreadsheet,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (_looks_like_pptx),
    "application/vnd.ms-excel": _looks_like_ole2,
    "application/msword": _looks_like_ole2,
    "application/vnd.ms-powerpoint": _looks_like_ole2,
    "image/png": _looks_like_png,
    "image/jpeg": _looks_like_jpeg,
    "image/tiff": _looks_like_tiff,
    "image/gif": _looks_like_gif,
    "image/bmp": _looks_like_bmp,
    "image/webp": _looks_like_webp,
    # Texte brut : aucun octet magique à vérifier, acceptés explicitement
    # (cf. docstring de module) plutôt qu'absents de cette table.
    "text/csv": None,
    "application/csv": None,
    "text/plain": None,
    "text/markdown": None,
    "text/html": None,
    "application/json": None,
    "message/rfc822": None,
}


def ensure_content_matches_signature(content: bytes, content_type: str) -> None:
    """Rejette un contenu dont les octets ne correspondent pas à `content_type`.

    Appelée après `ensure_upload_within_limits` (même point, `DocumentService.
    register`) : `content_type` a donc déjà franchi la liste blanche. Un type
    absent de `_SIGNATURE_CHECKS` (ne devrait pas arriver, vu l'ordre d'appel)
    est traité comme sans signature à vérifier plutôt que rejeté à l'aveugle —
    la liste blanche reste la seule source de vérité sur les types acceptés.

    Raises:
        UnsupportedMediaTypeError: signature absente ou incohérente avec le
            type retenu (`415`) — fichier renommé, vide ou tronqué.
    """
    checker = _SIGNATURE_CHECKS.get(content_type)
    if checker is None:
        return
    if not checker(content):
        raise UnsupportedMediaTypeError(
            "Le contenu du fichier ne correspond pas au type de document déclaré "
            f"« {content_type} » — vérifiez qu'il n'a pas été renommé ou corrompu.",
            extensions={"content_type": content_type},
        )
