"""Garde-fou qualité d'extraction — policy de seuils (Phase 11 Étape 4, ADR-024).

Ce module teste `app.ingestion.quality` en isolation, sans pipeline ni base de
données : les signaux et la policy qui en tire (au plus) un `JobWarning`. Le
câblage pipeline (les deux points d'écriture de `JobState.COMPLETED`, la
non-régression de leur synchronisation, le fait que le calcul ne bloque
jamais) est testé séparément dans
`tests/workers/test_ingestion_quality_guard.py`.

Trois faits motivent explicitement ces cas de test (ADR-024 §3, informés par
la baseline `run-20260807-104858` (mesure interne)) :

- `25-pdf-single-word.pdf` — 100 % de fragments dégénérés par construction
  (10 caractères significatifs au total) : faux positif connu, à exclure ;
- le plancher légitime le plus bas de couverture source (44,4 %,
  en-têtes/pieds de page répétés) ne doit jamais déclencher ;
- `word_recognition_rate` (hors périmètre v1, non porté ici) renvoie déjà
  `None` sur un document CJK — le principe « un `None` n'est jamais un
  échec » est ce que ces tests verrouillent pour les métriques que ce module
  calcule réellement (`degenerate_chunk_pct`, `source_coverage`).
"""

from __future__ import annotations

from app.core.config import Settings
from app.ingestion.quality import (
    QualitySignals,
    compute_quality_signals,
    evaluate_document_quality,
    evaluate_quality,
    significant_char_count,
    source_coverage,
)

SETTINGS = Settings()
"""Seuils par défaut (`app/core/config.py`) : 20 caractères, 50 %, plancher de
100 caractères significatifs, couverture minimale 30 %."""


# ─── `significant_char_count` ─────────────────────────────────────────────────


def test_significant_char_count_ignores_all_whitespace() -> None:
    assert significant_char_count("  a b\n\tc  ") == 3


def test_significant_char_count_of_empty_text_is_zero() -> None:
    assert significant_char_count("") == 0


# ─── `source_coverage` ─────────────────────────────────────────────────────────


def test_source_coverage_is_none_without_any_source_text() -> None:
    """Page probablement scannée : pas de couche de texte source à comparer."""
    assert source_coverage("", "peu importe le contenu des fragments") is None


def test_source_coverage_is_perfect_when_chunks_reproduce_the_source() -> None:
    text = "Le chiffre d'affaires progresse de douze pour cent sur l'ensemble des activites."
    assert source_coverage(text, text) == 1.0


def test_source_coverage_drops_when_most_source_text_is_missing() -> None:
    source = "Le chiffre d'affaires progresse de douze pour cent sur l'ensemble des activites."
    chunks = "Un contenu entierement different, sans aucun rapport avec le texte source."
    coverage = source_coverage(source, chunks)
    assert coverage is not None
    assert coverage < 0.30


# ─── `compute_quality_signals` ─────────────────────────────────────────────────


def test_zero_chunks_yields_signals_with_none_metrics() -> None:
    signals = compute_quality_signals(
        [], is_pdf=True, pdf_source_text="peu importe", degenerate_chunk_chars=20
    )
    assert signals == QualitySignals(
        chunk_count=0, significant_char_total=0, degenerate_chunk_pct=None, source_coverage=None
    )


def test_non_pdf_documents_never_get_a_source_coverage_value() -> None:
    """Hors périmètre v1 (ADR-024 §3) : pas de référence indépendante pour le non-PDF."""
    signals = compute_quality_signals(
        ["un fragment tout à fait ordinaire"],
        is_pdf=False,
        pdf_source_text=None,
        degenerate_chunk_chars=20,
    )
    assert signals.source_coverage is None


def test_a_pdf_without_extractable_source_text_gets_no_coverage_value() -> None:
    """Page scannée : `pdf_source_text is None` (extraction impossible), pas 0 %."""
    signals = compute_quality_signals(
        ["un fragment"], is_pdf=True, pdf_source_text=None, degenerate_chunk_chars=20
    )
    assert signals.source_coverage is None


# ─── `evaluate_quality` — policy de seuils (ADR-024 §3) ────────────────────────


def test_zero_chunks_always_triggers_a_warning() -> None:
    signals = QualitySignals(
        chunk_count=0, significant_char_total=0, degenerate_chunk_pct=None, source_coverage=None
    )
    warning = evaluate_quality(signals, settings=SETTINGS)
    assert warning is not None
    assert warning.code == "zero_chunks"


def test_a_healthy_document_gets_no_warning() -> None:
    """Chemin nominal de la baseline (`01-pdf-single-column-en.pdf`) : rien ne se déclenche."""
    signals = QualitySignals(
        chunk_count=1, significant_char_total=715, degenerate_chunk_pct=0.0, source_coverage=1.0
    )
    assert evaluate_quality(signals, settings=SETTINGS) is None


def test_the_lowest_known_legitimate_coverage_never_triggers() -> None:
    """`14-pdf-headers-footers-repeated.pdf` (44,4 %) reste sous le seuil par défaut (30 %)."""
    signals = QualitySignals(
        chunk_count=1, significant_char_total=584, degenerate_chunk_pct=0.0, source_coverage=0.444
    )
    assert evaluate_quality(signals, settings=SETTINGS) is None


def test_severe_source_coverage_loss_triggers_a_warning() -> None:
    """Le cas motivant l'ADR : `chunk_method=email` sur un PDF, couverture à 2,2 %."""
    signals = QualitySignals(
        chunk_count=3, significant_char_total=900, degenerate_chunk_pct=0.0, source_coverage=0.022
    )
    warning = evaluate_quality(signals, settings=SETTINGS)
    assert warning is not None
    assert warning.code == "low_source_coverage"
    assert warning.metrics["source_coverage"] == 0.022


def test_a_short_legitimate_document_is_excluded_from_the_degenerate_check() -> None:
    """`25-pdf-single-word.pdf` : 100 % dégénéré par construction, pas un défaut (ADR-024 §3).

    `source_coverage=1.0` (comme dans la baseline réelle, où le mot unique du
    PDF est bien retrouvé tel quel dans son unique fragment) : sans
    l'exclusion, `degenerate_chunk_pct=1.0` déclencherait `degenerate_content`
    à tort — c'est précisément le faux positif documenté que ce test verrouille.
    """
    signals = QualitySignals(
        chunk_count=1, significant_char_total=10, degenerate_chunk_pct=1.0, source_coverage=1.0
    )
    assert evaluate_quality(signals, settings=SETTINGS) is None


def test_degenerate_content_triggers_once_the_document_is_long_enough() -> None:
    """Le même taux de dégénérescence, mais sur un document qui n'est plus « court »."""
    signals = QualitySignals(
        chunk_count=10,
        significant_char_total=150,  # au-dessus du plancher par défaut (100)
        degenerate_chunk_pct=0.9,
        source_coverage=1.0,
    )
    warning = evaluate_quality(signals, settings=SETTINGS)
    assert warning is not None
    assert warning.code == "degenerate_content"


def test_a_minority_of_degenerate_chunks_does_not_trigger() -> None:
    """Un unique fragment-déchet perdu dans un document par ailleurs sain ne doit pas alerter."""
    signals = QualitySignals(
        chunk_count=20,
        significant_char_total=5000,
        degenerate_chunk_pct=0.05,
        source_coverage=0.9,
    )
    assert evaluate_quality(signals, settings=SETTINGS) is None


def test_skip_degenerate_check_silences_a_would_be_trigger() -> None:
    """`use_parent_child` (Phase 13) : les fragments enfants sont courts par construction.

    Mêmes signaux que `test_degenerate_content_triggers_once_the_document_is_
    long_enough` (qui déclenche sans l'option) — `skip_degenerate_check=True`
    doit neutraliser exactement cette branche, sans rien changer aux autres.
    Mesuré en test de bout en bout (Phase 13, `11-pdf-long-multipage.pdf`) :
    32 % de fragments « dégénérés » sur un document par ailleurs sain une fois
    `use_parent_child` actif — ce test verrouille le garde-fou du garde-fou.
    """
    signals = QualitySignals(
        chunk_count=10,
        significant_char_total=150,
        degenerate_chunk_pct=0.9,
        source_coverage=1.0,
    )
    assert evaluate_quality(signals, settings=SETTINGS, skip_degenerate_check=True) is None


def test_skip_degenerate_check_does_not_silence_zero_chunks() -> None:
    """L'exclusion Phase 13 ne porte que sur `degenerate_content` — signal univoque intact."""
    signals = QualitySignals(
        chunk_count=0, significant_char_total=0, degenerate_chunk_pct=None, source_coverage=None
    )
    warning = evaluate_quality(signals, settings=SETTINGS, skip_degenerate_check=True)
    assert warning is not None
    assert warning.code == "zero_chunks"


def test_skip_degenerate_check_does_not_silence_low_source_coverage() -> None:
    """L'exclusion Phase 13 ne porte que sur `degenerate_content` — la couverture reste active.

    `use_parent_child` change la taille des fragments, pas le texte total
    retrouvé : `source_coverage` reste un signal fiable, doit continuer à
    déclencher normalement.
    """
    signals = QualitySignals(
        chunk_count=10, significant_char_total=150, degenerate_chunk_pct=0.9, source_coverage=0.05
    )
    warning = evaluate_quality(signals, settings=SETTINGS, skip_degenerate_check=True)
    assert warning is not None
    assert warning.code == "low_source_coverage"


def test_a_none_degenerate_pct_is_never_treated_as_a_failure() -> None:
    """Signal absent (par construction, `chunk_count == 0` déjà couvert plus haut) : ignoré.

    Ce cas ne peut survenir qu'avec `chunk_count > 0` explicitement forcé ici
    pour isoler la règle « `None` n'est jamais un échec » (ADR-024 §3) du cas
    `zero_chunks`, déjà couvert par son propre test.
    """
    signals = QualitySignals(
        chunk_count=1, significant_char_total=500, degenerate_chunk_pct=None, source_coverage=1.0
    )
    assert evaluate_quality(signals, settings=SETTINGS) is None


def test_a_none_source_coverage_is_never_treated_as_a_failure() -> None:
    """Document non-PDF, ou PDF sans couche de texte source : jamais un échec en soi."""
    signals = QualitySignals(
        chunk_count=1, significant_char_total=500, degenerate_chunk_pct=0.0, source_coverage=None
    )
    assert evaluate_quality(signals, settings=SETTINGS) is None


def test_zero_chunks_takes_priority_over_the_other_signals() -> None:
    """Ordre de la policy (ADR-024 §3) : 0 chunk d'abord, signal univoque."""
    signals = QualitySignals(
        chunk_count=0, significant_char_total=0, degenerate_chunk_pct=None, source_coverage=None
    )
    warning = evaluate_quality(signals, settings=SETTINGS)
    assert warning is not None
    assert warning.code == "zero_chunks"


# ─── `evaluate_document_quality` — point d'entrée combiné ─────────────────────


def test_evaluate_document_quality_combines_computation_and_policy() -> None:
    text = "Le chiffre d'affaires progresse de douze pour cent sur l'ensemble des activites."
    warning = evaluate_document_quality(
        [text], is_pdf=True, pdf_source_text=text, settings=SETTINGS
    )
    assert warning is None


def test_evaluate_document_quality_on_an_empty_document() -> None:
    warning = evaluate_document_quality(
        [], is_pdf=True, pdf_source_text="peu importe", settings=SETTINGS
    )
    assert warning is not None
    assert warning.code == "zero_chunks"


def test_evaluate_document_quality_skips_degenerate_check_for_parent_child() -> None:
    """`skip_degenerate_check` traverse `evaluate_document_quality` jusqu'à la policy.

    Beaucoup de fragments d'une ligne (source réaliste d'un `use_parent_child`
    actif, cf. `_quality_warning_for_step`) mais le texte source est intégralement
    retrouvé (`source_coverage=1.0`) : aucun avertissement, alors que sans
    `skip_degenerate_check` ce même jeu de fragments déclencherait
    `degenerate_content` (chaque ligne < 20 caractères significatifs).
    """
    source = (
        "Alpha.\nBravo.\nCharlie.\nDelta.\nEcho.\nFoxtrot.\nGolf.\nHotel.\nIndia.\nJuliet.\n" * 5
    )
    chunks = source.strip().split("\n")
    warning = evaluate_document_quality(
        chunks,
        is_pdf=True,
        pdf_source_text=source,
        settings=SETTINGS,
        skip_degenerate_check=True,
    )
    assert warning is None
