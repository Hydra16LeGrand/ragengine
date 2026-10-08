"""Analyse légère d'un PDF : pages, texte extractible, tableaux réglés, images.

**Bibliothèque : `pdfplumber`** (MIT, au-dessus de `pdfminer.six`). Un seul
parseur couvre les quatre signaux dont l'écran d'aiguillage a besoin — nombre de
pages, texte réellement extractible, structures tabulaires, images incrustées —
là où `pypdf` seul ne sait rien dire des tableaux. Aucune dépendance GPU, aucun
modèle : **ce module ne fait pas d'OCR** et n'a pas vocation à en faire. Savoir
si une page *devrait* être océrisée suffit à poser la question à l'utilisateur ;
l'OCR réel appartient à RAGFlow, une fois la décision prise
(`docs/ingestion-engine-architecture.md` §4).

## Seuils retenus

`MIN_TEXT_CHARS_PER_PAGE = 50` — une page de texte courant porte plusieurs
centaines de caractères ; une page numérisée en porte zéro, ou une poignée
venant d'un tampon, d'un filigrane ou d'un numéro de page inséré après coup. Le
seuil est assez haut pour absorber ce bruit et assez bas pour ne jamais
disqualifier une vraie page de texte, même clairsemée (page de garde, titre de
partie).

`FULL_PAGE_IMAGE_COVERAGE = 0.8` + `SPARSE_TEXT_CHARS_PER_PAGE = 200` — second
signal, pour le cas que le seul comptage de caractères rate : une page
numérisée dont seul l'en-tête a été ressaisi en texte. Une image couvrant plus
de 80 % de la page **et** moins de 200 caractères de texte, c'est une
numérisation légendée, pas une page rédigée.

`ILLUSTRATED_PAGE_MIN_CHARS = 20` + `ILLUSTRATED_PAGE_MIN_COVERAGE = 0.1` —
contre-signal, pour le cas symétrique : une page « titre court + schéma » n'est
pas une page numérisée. Sans lui, un document de deux pages dont une porte une
illustration atteint un ratio de 0,5 et se voit routé vers DeepDOC sans que
l'utilisateur puisse s'y opposer (`chunking_profile.py`, garde
`force_deep_doc or is_likely_scanned`). Une page est donc rendue à son statut de
page rédigée quand elle réunit **une légende** (au moins 20 caractères : mesuré
à 28 sur le cas réel, contre 0 sur toute page numérisée du corpus, en-tête
ressaisi compris — celui-ci reste rattrapé par sa couverture d'image de 1,0) et
**une image qui ne remplit pas la page** (entre 10 % et 80 % : mesuré à 17,5 %
sur le cas réel, contre 100 % sur les sept documents numérisés du corpus). Une
illustration *sans* légende reste comptée comme numérisée : rien ne la distingue
d'un scan, et le doute profite à l'OCR.

Le plancher de pages analysées, envisagé pour le même défaut, a été écarté :
un scan d'une seule page cesserait d'être détecté, et le corpus en contient.

`SCANNED_DOCUMENT_RATIO = 0.3` — le document entier est déclaré probablement
numérisé dès qu'un tiers des pages analysées l'est. Un rapport de cent pages
dont trente sont des annexes scannées justifie déjà l'OCR renforcé ; exiger la
majorité des pages priverait l'utilisateur de l'option précisément là où elle
sert. Le ratio brut est exposé dans le rapport, pour que l'utilisateur puisse
trancher autrement.

`MAX_ANALYZED_PAGES = 24` — le precheck est une analyse rapide, pas une
indexation. Mesuré à environ 40 ms par page dense (texte + tableau réglé), soit
moins d'une seconde au total. Au-delà de 24 pages, l'échantillon est réparti
régulièrement sur tout le document (premières **et** dernières pages) : les
annexes scannées d'un rapport sont à la fin, un échantillon en tête les
manquerait systématiquement.

`PAGE_BUDGET_SECONDS = 8` — le plafond de pages ne suffit pas à borner le temps :
une page peut coûter très cher à elle seule. Mesuré sur un formulaire réel
(`/usr/share/cups/data/form_english.pdf`, 19 313 rectangles sur une seule page) :
**1,3 s pour cette page**, contre 40 ms pour une page ordinaire. Vingt-quatre
pages de ce genre feraient une minute d'attente devant un écran d'aiguillage
censé s'afficher tout de suite. L'échantillonnage s'interrompt donc dès le budget
dépassé — jamais avant la première page — et `analyzed_page_count` dit
exactement ce qui a été vu.
"""

from __future__ import annotations

import io
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Final

import pdfplumber
from pdfplumber.page import Page

from app.ingestion.precheck.columns import page_has_two_columns
from app.ingestion.precheck.extraction import ExtractionError, ExtractionOutcome

_logger = logging.getLogger(__name__)

PDF_MAGIC: Final = b"%PDF-"
"""En-tête de fichier PDF, cherché dans les premiers octets (cf. `looks_like_pdf`)."""

PDF_MAGIC_SEARCH_BYTES: Final = 1024
"""Certains PDF valides font précéder l'en-tête de quelques octets parasites."""

MAX_ANALYZED_PAGES: Final = 24
PAGE_BUDGET_SECONDS: Final = 8.0
MIN_TEXT_CHARS_PER_PAGE: Final = 50
SPARSE_TEXT_CHARS_PER_PAGE: Final = 200
FULL_PAGE_IMAGE_COVERAGE: Final = 0.8
ILLUSTRATED_PAGE_MIN_CHARS: Final = 20
ILLUSTRATED_PAGE_MIN_COVERAGE: Final = 0.1
SCANNED_DOCUMENT_RATIO: Final = 0.3
MAX_TEXT_SAMPLE_CHARS: Final = 100_000
"""Plafond de texte conservé pour la détection de langue — jamais persisté."""

HEADER_FOOTER_MIN_PAGES: Final = 3
"""En dessous, trop peu de pages pour distinguer un en-tête répété d'un texte
court par nature (page de garde, titre isolé) : la mesure ne serait pas fiable."""

HEADER_FOOTER_MIN_SHARE: Final = 0.5
"""Part des pages échantillonnées où une même ligne (numéro de page mis à part)
doit apparaître pour être reconnue comme en-tête/pied de page plutôt que
contenu. Mesuré sur un cas réel : l'en-tête de titre d'un PDF issu d'un export
bureautique (InDesign/QuarkXPress) — nom de fichier, date, « Page N » — se
retrouve sur la totalité des pages échantillonnées."""

_DIGIT_RUN: Final = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class _PageSignals:
    """Ce qu'une page apporte au rapport."""

    text: str
    character_count: int
    is_scanned: bool
    table_count: int
    image_count: int
    has_two_columns: bool


def looks_like_pdf(content: bytes) -> bool:
    """Vérifie l'en-tête `%PDF-` : un `.pdf` qui n'en a pas est un fichier corrompu."""
    return PDF_MAGIC in content[:PDF_MAGIC_SEARCH_BYTES]


def sample_page_indexes(page_count: int, limit: int) -> list[int]:
    """Échantillonne au plus `limit` indices de pages, répartis sur tout le document.

    Les deux extrémités sont toujours incluses : un document mixte (texte au
    début, annexes numérisées à la fin) doit être vu comme mixte.
    """
    if page_count <= 0:
        return []
    if page_count <= limit:
        return list(range(page_count))
    step = (page_count - 1) / (limit - 1)
    return sorted({round(rank * step) for rank in range(limit)})


def _image_coverage(page: Page) -> float:
    """Fraction de la page couverte par des images, plafonnée à 1.

    Les rectangles se chevauchant sont comptés deux fois : l'approximation
    surestime la couverture, dans le sens conservateur pour un seuil qui sert à
    *proposer* l'OCR, jamais à l'imposer.
    """
    area = float(page.width) * float(page.height)
    if area <= 0:
        return 0.0
    covered = 0.0
    for image in page.images:
        width = max(0.0, float(image["x1"]) - float(image["x0"]))
        height = max(0.0, float(image["bottom"]) - float(image["top"]))
        covered += width * height
    return min(covered / area, 1.0)


def _is_illustrated_page(character_count: int, coverage: float) -> bool:
    """Reconnaît une page « légende courte + illustration », qui n'est pas une numérisation.

    Les trois conditions vont ensemble : une légende (une page numérisée n'en a
    pas, sa couche texte est vide), et une image assez grande pour porter le
    contenu de la page mais pas assez pour être le raster d'un scan.
    """
    return (
        character_count >= ILLUSTRATED_PAGE_MIN_CHARS
        and ILLUSTRATED_PAGE_MIN_COVERAGE <= coverage < FULL_PAGE_IMAGE_COVERAGE
    )


def _analyze_page(page: Page) -> _PageSignals:
    """Extrait les signaux d'une page ouverte."""
    text = page.extract_text() or ""
    character_count = sum(1 for character in text if not character.isspace())
    images = page.images
    coverage = _image_coverage(page)
    is_scanned = (
        character_count < MIN_TEXT_CHARS_PER_PAGE
        and not _is_illustrated_page(character_count, coverage)
    ) or (character_count < SPARSE_TEXT_CHARS_PER_PAGE and coverage >= FULL_PAGE_IMAGE_COVERAGE)
    return _PageSignals(
        text=text,
        character_count=character_count,
        is_scanned=is_scanned,
        # Stratégie par défaut de pdfplumber : les tableaux sont reconnus à
        # leurs traits. Un tableau aligné à l'espace n'a aucun trait et passe
        # donc inaperçu — limite documentée au niveau du module.
        table_count=len(page.find_tables()),
        image_count=len(images),
        # Deux sections côte à côte : signal d'aiguillage, cf. `columns.py`. Sur
        # une page numérisée, il n'y a pas de couche texte à mesurer et le
        # détecteur rend `False` de lui-même — c'est le bon verdict, `Plain Text`
        # n'y produisant de toute façon rien (vérifié : 0 fragment, 0 caractère).
        has_two_columns=page_has_two_columns(page),
    )


def _normalize_line_for_repetition(line: str) -> str:
    """Ramène une ligne à une forme comparable d'une page à l'autre.

    Un numéro de page ou un horodatage change à chaque page sans changer la
    nature de la ligne : les suites de chiffres sont donc effacées avant
    comparaison, sans quoi « Page 6 » et « Page 12 » seraient vues comme deux
    lignes distinctes et l'en-tête ne serait jamais reconnu comme répété.
    """
    return _DIGIT_RUN.sub("#", line.strip().lower())


def _strip_repeated_lines(page_texts: list[str]) -> list[str]:
    """Retire les en-têtes/pieds de page répétés sur la majorité des pages.

    Un export bureautique grave parfois un en-tête de titre identique sur
    chaque page — y compris quand le corps de la page est une image sans
    couche texte sélectionnable. Sur un tel document, cet en-tête devient
    alors l'essentiel, voire la totalité, du texte extrait : `detect_languages`
    (`languages.py`) n'a plus qu'un fragment répétitif de quelques dizaines de
    caractères (nom de fichier, « Page N ») à juger, et un détecteur par
    n-grammes s'y trompe avec assurance dans un sens comme dans l'autre — pas
    parce que le document change de langue, mais parce que ce fragment n'est
    représentatif de rien. Cas réel : `Journal officiel OHADA ...qxp_OHADA
    ...Page 6`, classé anglais à confiance 1.0 sur un acte uniforme rédigé en
    français.

    Seules la **première et la dernière ligne** de chaque page sont candidates :
    structurellement, c'est là que vivent un en-tête ou un pied de page, jamais
    au milieu d'un corps de texte. Sans cette restriction, un document dont
    deux pages partagent un paragraphe identique par ailleurs (gabarit, mention
    légale reprise mot pour mot) se ferait vider de tout son texte — mesuré sur
    un PDF de test à pages volontairement identiques, qui perdait alors sa
    langue détectée.
    """
    if len(page_texts) < HEADER_FOOTER_MIN_PAGES:
        return page_texts

    split_pages = [text.splitlines() for text in page_texts]

    def _edge_lines(lines: list[str]) -> set[str]:
        candidates = set()
        if lines and lines[0].strip():
            candidates.add(_normalize_line_for_repetition(lines[0]))
        if len(lines) > 1 and lines[-1].strip():
            candidates.add(_normalize_line_for_repetition(lines[-1]))
        return candidates

    line_page_counts: dict[str, int] = defaultdict(int)
    for lines in split_pages:
        for normalized in _edge_lines(lines):
            line_page_counts[normalized] += 1

    threshold = max(HEADER_FOOTER_MIN_PAGES, round(len(page_texts) * HEADER_FOOTER_MIN_SHARE))
    repeated = {line for line, count in line_page_counts.items() if count >= threshold}
    if not repeated:
        return page_texts

    stripped_pages: list[str] = []
    for lines in split_pages:
        kept = list(lines)
        if kept and _normalize_line_for_repetition(kept[0]) in repeated:
            kept = kept[1:]
        if kept and _normalize_line_for_repetition(kept[-1]) in repeated:
            kept = kept[:-1]
        stripped_pages.append("\n".join(kept))
    return stripped_pages


def analyze_pdf(content: bytes) -> ExtractionOutcome:
    """Produit les signaux structurels d'un PDF.

    Lève `ExtractionError` si le fichier est illisible de bout en bout ; une
    page défaillante isolée est seulement journalisée et exclue de
    l'échantillon, le reste du document restant exploitable.
    """
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            indexes = sample_page_indexes(page_count, MAX_ANALYZED_PAGES)
            analyzed = 0
            scanned = 0
            characters = 0
            tables = 0
            images = 0
            two_column_pages = 0
            texts: list[str] = []
            sample_length = 0
            deadline = time.monotonic() + PAGE_BUDGET_SECONDS

            for index in indexes:
                if analyzed and time.monotonic() >= deadline:
                    _logger.info(
                        "Precheck écourté : budget de %.0f s atteint après %d pages sur %d",
                        PAGE_BUDGET_SECONDS,
                        analyzed,
                        page_count,
                    )
                    break
                page = pdf.pages[index]
                try:
                    signals = _analyze_page(page)
                except Exception:  # pragma: no cover - dépend d'un PDF réellement abîmé
                    _logger.warning("Page %d illisible, exclue du precheck", index, exc_info=True)
                    continue
                finally:
                    # pdfminer met en cache les objets de chaque page : sans
                    # cela, un PDF volumineux tiendrait entièrement en mémoire.
                    page.close()

                analyzed += 1
                scanned += int(signals.is_scanned)
                characters += signals.character_count
                tables += signals.table_count
                images += signals.image_count
                two_column_pages += int(signals.has_two_columns)
                if sample_length < MAX_TEXT_SAMPLE_CHARS and signals.text:
                    texts.append(signals.text)
                    sample_length += len(signals.text)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"{type(exc).__name__}: {exc}") from exc

    if page_count > 0 and analyzed == 0:
        raise ExtractionError("Aucune page du document n'a pu être analysée.")

    ratio = scanned / analyzed if analyzed else None
    return ExtractionOutcome(
        page_count=page_count,
        analyzed_page_count=analyzed,
        text_character_count=characters,
        is_likely_scanned=ratio is not None and ratio >= SCANNED_DOCUMENT_RATIO,
        scanned_page_ratio=ratio,
        table_count=tables,
        figure_count=images,
        two_column_page_count=two_column_pages,
        text_sample="\n\n".join(_strip_repeated_lines(texts))[:MAX_TEXT_SAMPLE_CHARS],
    )
