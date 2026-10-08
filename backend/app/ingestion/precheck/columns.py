"""Détection de **deux sections de texte côte à côte** sur une page PDF.

Pourquoi ce signal existe
-------------------------
Mesuré le 2026-08-22 (ADR-042, `column-reading-order-*` (mesure interne)) :
sur un PDF à deux colonnes, `layout_recognize: "DeepDOC"` rend le texte **ligne
par ligne en traversant les colonnes**, pas colonne par colonne. Ce n'est pas une
dérive de qualité, c'est l'ordre de lecture lui-même :

| document | lecture correcte | lecture entrelacée | DeepDOC mesuré | Plain Text mesuré |
|---|---|---|---|---|
| `02-pdf-multi-column-cv-fr.pdf` | 0,020 | 0,321 | **0,283** | 0,057 |
| `real-notice-2042.pdf` (40 p.) | 0,014 | 0,167 | **0,168** | 0,021 |

(taux de basculement de colonne entre deux mots consécutifs ; les deux premières
colonnes sont calculées sur le PDF source et bornent l'échelle.)

`DeepDOC` tombe donc **exactement sur la borne entrelacée**. La cause est dans le
moteur, à un endroit qui ne se règle pas : `rag/app/naive.py` termine sa chaîne
PDF par `_concat_downward()`, qui se réduit à `Recognizer.sort_Y_firstly(boxes,
0)` — un tri global par altitude, c'est-à-dire un ordre ligne par ligne. La passe
qui trierait par colonne, `_final_reading_order_merge()`, existe à la ligne
suivante et y est **commentée**. Même mécanisme que celui déjà relevé par
ADR-038 pour les marqueurs de liste.

Ce module ne corrige rien dans le moteur (ADR-001) : il **détecte** le cas et
laisse le precheck le porter à la connaissance de l'utilisateur, qui arbitre
document par document — l'architecture voulue par ADR-031.

Ce qui est détecté, et ce qui ne l'est pas
-----------------------------------------
Une **gouttière** est une bande verticale sans aucun mot, assez large *et* assez
haute pour traverser la zone de texte. La hauteur est le critère qui sépare une
mise en page en colonnes d'un simple espacement de tableau : les blancs entre
cellules sont interrompus dès qu'une ligne pleine largeur les traverse.

Seules les pages coupées en **exactement deux bandes** sont comptées. C'est
volontairement restrictif : c'est le cas remonté (« deux sections côte à côte »),
et c'est ce qui écarte les tableaux, qui se présentent au même détecteur comme
quatre ou cinq bandes. Vérifié sur le corpus versionné — fire sur le CV à deux
colonnes, la notice 2042, l'article ACL et le cerfa 2035 ; silencieux sur
`03-pdf-ruled-table` (4 bandes), `17-pdf-table-spanning-pages` (4),
`18-pdf-table-borderless` (4), `19-pdf-table-large-200rows` (5), ainsi que sur
tous les documents mono-colonne.

Un tableau à deux colonnes serait, lui, compté à tort : la géométrie seule ne
l'en distingue pas. Le rapport de precheck expose donc `table_count` à côté de ce
signal, et c'est l'humain qui tranche — jamais ce module.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Final

from pdfplumber.page import Page

LINE_CLUSTER_TOLERANCE_PT: Final = 3.0
"""Deux mots dont les sommets diffèrent de moins de ceci sont sur la même ligne visuelle."""

X_SCAN_STEP_PT: Final = 3.0
"""Pas du balayage horizontal.

Un pas de 1 pt ne change aucun verdict sur le corpus versionné et coûte plus de
cent millisecondes par page dense — hors budget pour un precheck plafonné à 8 s
tous signaux confondus (`pdf.PAGE_BUDGET_SECONDS`).
"""

GUTTER_MIN_WIDTH_RATIO: Final = 0.03
"""Largeur minimale d'une gouttière, en fraction de la largeur de page."""

GUTTER_MIN_HEIGHT_RATIO: Final = 0.40
"""Hauteur libre minimale d'une gouttière, en fraction de la zone de texte de la page.

C'est ce seuil qui distingue une colonne d'un espacement de tableau : une
gouttière de colonnes court sur la hauteur du corps, un blanc inter-cellules est
coupé par la première ligne pleine largeur.
"""

GUTTER_MARGIN_RATIO: Final = 0.12
"""Bornes de recherche, en fraction de la largeur : ni la marge gauche ni la marge droite."""

MIN_FLANKING_LINES: Final = 3
"""Lignes devant porter du texte **des deux côtés** pour qu'une gouttière en soit une.

Sans ce contrôle, le bord droit du texte passe pour une gouttière dès qu'une
seule ligne dépasse les autres — faux positif observé sur
`02-pdf-multi-column-cv-fr.pdf` avant l'ajout de ce critère.
"""

MIN_WORDS_PER_PAGE: Final = 20
"""En dessous, une page est trop pauvre pour que sa géométrie veuille dire quoi que ce soit."""

TWO_COLUMNS: Final = 2
"""Nombre de bandes qui définit « deux sections côte à côte » (cf. docstring de module)."""


def _cluster_lines(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        buckets[round(float(word["top"]) / LINE_CLUSTER_TOLERANCE_PT)].append(word)
    return [line for _, line in sorted(buckets.items(), key=lambda item: item[0])]


def _free_height_by_column(
    lines: list[list[dict[str, Any]]],
    first_x: int,
    last_x: int,
    page_top: float,
    page_bottom: float,
) -> dict[int, float]:
    """Pour chaque abscisse balayée, la plus grande hauteur continue sans aucun mot.

    Le balayage travaille sur les **lignes** et non sur les mots : une ligne
    occupe une abscisse dès qu'un de ses mots la couvre, et une page compte
    quelques dizaines de lignes contre plusieurs centaines de mots. Le tri par
    abscisse, qui domine le coût, s'en trouve réduit d'un ordre de grandeur —
    sans changer le résultat, puisque deux mots d'une même ligne partagent la
    même altitude.
    """
    occupied: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for line in lines:
        top = min(float(word["top"]) for word in line)
        bottom = max(float(word["bottom"]) for word in line)
        covered: set[int] = set()
        for word in line:
            start = int(float(word["x0"]) // X_SCAN_STEP_PT)
            end = int(float(word["x1"]) // X_SCAN_STEP_PT)
            covered.update(range(start, end + 1))
        for step in covered:
            if first_x <= step <= last_x:
                occupied[step].append((top, bottom))

    free: dict[int, float] = {}
    for step in range(first_x, last_x + 1):
        spans = sorted(occupied.get(step, ()))
        best, cursor = 0.0, page_top
        for top, bottom in spans:
            best = max(best, top - cursor)
            cursor = max(cursor, bottom)
        free[step] = max(best, page_bottom - cursor)
    return free


def _is_flanked(left: float, right: float, lines: list[list[dict[str, Any]]]) -> bool:
    """Vrai si assez de lignes portent du texte de part et d'autre de la bande."""
    flanked = 0
    for line in lines:
        has_left = any(float(word["x1"]) <= left for word in line)
        has_right = any(float(word["x0"]) >= right for word in line)
        if has_left and has_right:
            flanked += 1
            if flanked >= MIN_FLANKING_LINES:
                return True
    return False


def detect_gutters(page: Page) -> list[tuple[float, float]]:
    """Bandes verticales séparant des sections de texte côte à côte, de gauche à droite.

    Rend une liste vide pour une page mono-colonne, vide, ou trop pauvre en mots.
    """
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if len(words) < MIN_WORDS_PER_PAGE:
        return []
    page_width = float(page.width)
    page_top = min(float(word["top"]) for word in words)
    page_bottom = max(float(word["bottom"]) for word in words)
    if page_bottom <= page_top or page_width <= 0:
        return []

    lines = _cluster_lines(words)
    min_height = (page_bottom - page_top) * GUTTER_MIN_HEIGHT_RATIO
    min_width = page_width * GUTTER_MIN_WIDTH_RATIO
    first_x = int(page_width * GUTTER_MARGIN_RATIO // X_SCAN_STEP_PT)
    last_x = int(page_width * (1.0 - GUTTER_MARGIN_RATIO) // X_SCAN_STEP_PT)
    if last_x <= first_x:
        return []

    free = _free_height_by_column(lines, first_x, last_x, page_top, page_bottom)
    gutters: list[tuple[float, float]] = []
    start: int | None = None
    for step in range(first_x, last_x + 2):
        if free.get(step, 0.0) >= min_height:
            if start is None:
                start = step
            continue
        if start is not None:
            left, right = start * X_SCAN_STEP_PT, step * X_SCAN_STEP_PT
            if right - left >= min_width and _is_flanked(left, right, lines):
                gutters.append((left, right))
            start = None
    return gutters


def page_has_two_columns(page: Page) -> bool:
    """Vrai si la page est coupée en **exactement deux** sections côte à côte.

    Restriction assumée, pas une approximation : au-delà de deux bandes, la
    géométrie décrit un tableau bien plus souvent qu'une mise en page en
    colonnes (cf. docstring de module).
    """
    return len(detect_gutters(page)) == TWO_COLUMNS - 1
