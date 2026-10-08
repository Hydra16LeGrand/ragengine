"""Garde-fou qualité d'extraction — calcul du `JobWarning` (Phase 11 Étape 4, ADR-024).

Porte dans `app/` la logique déjà écrite et vérifiée par le harnais de l'étape 0
(`measure_chunking_quality.py` (outil interne) : `significant_char_count`,
`source_coverage`, `DEGENERATE_CHUNK_THRESHOLD_CHARS`) plutôt que de la
redévelopper à la main — mêmes définitions, seuils désormais configurables via
`Settings` (`app.core.config`) au lieu d'être des constantes de script.

**Résumé (ADR-024 §3)** — seuls trois signaux jugés fiables déclenchent un
avertissement, un seul à la fois (le plus sévère l'emporte, cf. `evaluate_quality`) :

1. `zero_chunks` — 0 fragment après complétion, tout format. Signal univoque.
2. `degenerate_content` — part de fragments dégénérés au-delà du seuil, **en
   excluant les documents dont le total de caractères significatifs est
   lui-même très court** (`25-pdf-single-word.pdf`, 100 % « dégénéré » par
   construction, pas un défaut — faux positif connu et documenté).
3. `low_source_coverage` — couverture du texte source **PDF uniquement**
   (référence `pdfplumber`) sous un seuil bas, nettement sous le plancher
   légitime connu (44,4 %, en-têtes/pieds de page répétés).

Hors périmètre v1, volontairement : entrelacement de colonnes, couverture
source non-PDF, taux de mots reconnus seul (faux positif certain sur tout
document CJK — cette classe de métrique renvoie déjà `None` dans ce cas, et ce
module ne traite jamais un `None` comme un échec).
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.domain.schemas.ingestion_job import JobWarning

if TYPE_CHECKING:  # pragma: no cover - imports de typage uniquement
    from app.core.config import Settings

_logger = logging.getLogger(__name__)

PDF_CONTENT_TYPE: Final = "application/pdf"

_WHITESPACE_RE: Final = re.compile(r"\s+", re.UNICODE)

_SHINGLE_SIZE: Final = 5
"""Même taille de n-gramme que `measure_chunking_quality.py` (outil interne) : signal
reproductible entre le harnais de mesure hors ligne et ce garde-fou en
production."""


def significant_char_count(text: str) -> int:
    """Caractères non-espace d'un texte — porté de `measure_chunking_quality.py` (outil interne)."""
    return len(_WHITESPACE_RE.sub("", text))


def _normalize_for_shingles(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()


def _shingles(text: str, n: int = _SHINGLE_SIZE) -> set[str]:
    normalized = _normalize_for_shingles(text)
    if len(normalized) < n:
        return {normalized} if normalized else set()
    return {normalized[i : i + n] for i in range(len(normalized) - n + 1)}


def source_coverage(source_text: str, chunks_concat: str) -> float | None:
    """Proportion des n-grammes de caractères du texte source retrouvés dans les fragments.

    Référence indépendante du moteur (`pdfplumber`, pas RAGFlow) : détecte le
    texte **perdu** à l'extraction. `None` si le PDF source n'a aucune couche
    de texte extractible (page probablement scannée) — distinct d'une
    couverture nulle, ne doit jamais être traité comme un échec. Porté de
    `measure_chunking_quality.py` (outil interne).
    """
    source_shingles = _shingles(source_text)
    if not source_shingles:
        return None
    chunk_shingles = _shingles(chunks_concat)
    if not chunk_shingles:
        return 0.0
    return len(source_shingles & chunk_shingles) / len(source_shingles)


def extract_pdf_source_text(content: bytes) -> str | None:
    """Texte source d'un PDF via `pdfplumber` — best-effort, jamais une exception.

    Ce module ne doit jamais faire échouer la complétion d'un job (ADR-024 :
    « ne bloque jamais »). `pdfminer` (sous `pdfplumber`) lève des exceptions
    non normalisées sur un PDF corrompu ou inattendu — capturées volontairement
    en `Exception` large ici, même choix assumé que le harnais de l'étape 0
    (`measure_chunking_quality.py::compute_chunk_metrics` (outil interne)).
    """
    import pdfplumber  # import local : évite de charger pdfplumber hors du besoin réel

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        _logger.warning(
            "PDF illisible par pdfplumber pour le garde-fou qualité — couverture ignorée.",
            exc_info=True,
        )
        return None


@dataclass(frozen=True, slots=True)
class QualitySignals:
    """Signaux mesurés sur les fragments finaux d'un document indexé."""

    chunk_count: int
    significant_char_total: int
    degenerate_chunk_pct: float | None
    """`None` seulement si `chunk_count == 0` (rien à mesurer)."""
    source_coverage: float | None
    """PDF uniquement (référence `pdfplumber`) ; `None` pour tout autre format,
    ou si le PDF n'a aucune couche de texte extractible."""


def compute_quality_signals(
    chunk_texts: list[str],
    *,
    is_pdf: bool,
    pdf_source_text: str | None,
    degenerate_chunk_chars: int,
) -> QualitySignals:
    """Dérive les signaux à partir des fragments finaux (et, pour un PDF, du texte source)."""
    if not chunk_texts:
        return QualitySignals(
            chunk_count=0,
            significant_char_total=0,
            degenerate_chunk_pct=None,
            source_coverage=None,
        )

    significant_counts = [significant_char_count(text) for text in chunk_texts]
    significant_total = sum(significant_counts)
    degenerate = sum(1 for count in significant_counts if count < degenerate_chunk_chars)
    degenerate_pct = degenerate / len(chunk_texts)

    coverage: float | None = None
    if is_pdf and pdf_source_text is not None:
        coverage = source_coverage(pdf_source_text, "\n".join(chunk_texts))

    return QualitySignals(
        chunk_count=len(chunk_texts),
        significant_char_total=significant_total,
        degenerate_chunk_pct=degenerate_pct,
        source_coverage=coverage,
    )


def evaluate_quality(
    signals: QualitySignals, *, settings: Settings, skip_degenerate_check: bool = False
) -> JobWarning | None:
    """Applique la policy de seuils (ADR-024 §3) : au plus un avertissement, le plus sévère.

    Ordre volontaire, reprenant celui de l'ADR : 0 chunk d'abord (signal
    univoque, aucun faux positif possible), puis chunks dégénérés, puis
    couverture source. Une métrique à `None` ne déclenche jamais rien — elle
    ne fait que ne pas participer à la décision (ex. `source_coverage` d'un
    document non-PDF, ou d'une page scannée sans couche de texte).

    `skip_degenerate_check` (Phase 13) : `True` quand `use_parent_child` était
    actif pour ce document — `list_document_chunks` y lit alors un mélange de
    fragments enfants (petits par construction, c'est le but de l'option) et
    de fragments « mère » plus longs (`GET .../chunks` ne filtre pas
    `available`), sans marqueur exploitable pour ne compter que les uns ou les
    autres (`mom_id` absent de cette réponse, fait vérifié). Mesuré en test de
    bout en bout, Phase 13 (`11-pdf-long-multipage.pdf`) : `degenerate_chunk_pct`
    grimpe à 32 % sur un document par ailleurs sain (`source_coverage=1.0`) —
    sous le seuil par défaut (50 %) sur ce cas précis, mais le seuil a été
    calibré sur du chunking `naive` (ADR-024 §3, Phase 11 Étape 0), pas sur des
    fragments volontairement courts : un document avec plus de lignes courtes
    (listes, en-têtes) franchirait facilement 50 % sans aucun défaut
    d'extraction réel. `source_coverage` reste pertinent et actif dans ce cas
    (il mesure le texte total retrouvé, pas la taille des fragments).
    """
    if signals.chunk_count == 0:
        return JobWarning(
            code="zero_chunks",
            message="Le document a terminé son indexation sans produire aucun fragment.",
            metrics={"chunk_count": 0},
        )

    if (
        not skip_degenerate_check
        and signals.significant_char_total
        >= settings.quality_guard_min_significant_chars_for_degenerate_check
        and signals.degenerate_chunk_pct is not None
        and signals.degenerate_chunk_pct >= settings.quality_guard_degenerate_chunk_pct_threshold
    ):
        return JobWarning(
            code="degenerate_content",
            message=(
                f"{signals.degenerate_chunk_pct * 100:.0f}% des fragments contiennent moins de "
                f"{settings.quality_guard_degenerate_chunk_chars} caractères significatifs."
            ),
            metrics={
                "degenerate_chunk_pct": signals.degenerate_chunk_pct,
                "chunk_count": signals.chunk_count,
                "significant_char_total": signals.significant_char_total,
            },
        )

    if (
        signals.source_coverage is not None
        and signals.source_coverage < settings.quality_guard_min_source_coverage
    ):
        return JobWarning(
            code="low_source_coverage",
            message=(
                f"Seuls {signals.source_coverage * 100:.1f}% du texte source du PDF ont été "
                "retrouvés dans les fragments indexés."
            ),
            metrics={
                "source_coverage": signals.source_coverage,
                "chunk_count": signals.chunk_count,
            },
        )

    return None


def evaluate_document_quality(
    chunk_texts: list[str],
    *,
    is_pdf: bool,
    pdf_source_text: str | None,
    settings: Settings,
    skip_degenerate_check: bool = False,
) -> JobWarning | None:
    """Point d'entrée unique du module : signaux puis policy, en un appel.

    Utilisé par `app.workers.tasks.ingestion._quality_warning_for_step`, le
    seul appelant — factorisé pour que les deux points d'écriture de
    `JobState.COMPLETED` (`_poll` et `_complete`) partagent exactement le même
    calcul (ADR-024, risque de couture oubliée entre deux points d'appel).

    `skip_degenerate_check` : transmis tel quel à `evaluate_quality` — voir sa
    docstring (interaction `use_parent_child`, Phase 13).
    """
    signals = compute_quality_signals(
        chunk_texts,
        is_pdf=is_pdf,
        pdf_source_text=pdf_source_text,
        degenerate_chunk_chars=settings.quality_guard_degenerate_chunk_chars,
    )
    return evaluate_quality(signals, settings=settings, skip_degenerate_check=skip_degenerate_check)
