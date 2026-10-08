"""Contrat interne entre l'orchestrateur du precheck et les extracteurs par format.

Un extracteur ne construit pas le rapport final : il produit les **signaux
structurels** de son format et un échantillon de texte. L'orchestrateur
(`analyzer.py`) y ajoute ce qui est commun à tous les formats — taille du
fichier, détection de langue — puis assemble le `PrecheckReport`.
"""

from __future__ import annotations

from dataclasses import dataclass


class ExtractionError(Exception):
    """Échec d'extraction attendu et explicable (fichier tronqué, chiffré, illisible).

    Purement interne : l'orchestrateur la traduit en rapport dégradé. Elle ne
    dérive volontairement pas de `RAGEngineError` — le precheck n'a aucune
    raison de faire échouer une requête HTTP.
    """


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    """Signaux structurels tirés d'un fichier par l'extracteur de son format."""

    page_count: int | None
    """Nombre de pages, `None` pour un format sans pagination."""

    analyzed_page_count: int
    """Pages réellement inspectées (l'analyse est bornée, cf. `pdf.MAX_ANALYZED_PAGES`)."""

    text_character_count: int
    """Caractères non blancs extraits des pages analysées."""

    is_likely_scanned: bool
    """Verdict « document numérisé » propre au format (toujours faux hors PDF)."""

    scanned_page_ratio: float | None
    """Part des pages analysées jugées numérisées, `None` si la notion ne s'applique pas."""

    table_count: int
    """Tableaux détectés sur les pages analysées."""

    figure_count: int
    """Images incrustées sur les pages analysées."""

    two_column_page_count: int
    """Pages analysées coupées en deux sections de texte côte à côte (toujours 0 hors PDF).

    Signal d'aiguillage, pas de qualité : mesuré (ADR-042), l'analyse de mise en
    page du moteur y rend le texte ligne par ligne en traversant les colonnes.
    Cf. `precheck/columns.py` pour ce qui est détecté et ce qui ne l'est pas.
    """

    text_sample: str
    """Texte servant à la détection de langue — borné, jamais persisté."""
