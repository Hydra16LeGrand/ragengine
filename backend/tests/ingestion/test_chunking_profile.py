"""Mapping decision → profil de chunking (`docs/ingestion-engine-architecture.md` §4).

Ce que ces tests verrouillent : les options du catalogue v1 sont
**indépendantes et cumulables**, aucune ne produit un `chunk_method` absent du
catalogue réel du moteur, `optimize_for_tables` ne produit `chunk_method=table`
que sur un tableur — RAGFlow le refuse explicitement sur un PDF (fait vérifié en
test de bout en bout : `file type not supported yet(excel, text, csv supported)`)
— et, depuis ADR-023 §3 (Phase 11, Étape 5), `layout_recognize` suit
`is_likely_scanned` sauf si `heavy_ocr`/`optimize_for_tables` (hors tableur)
imposent `DeepDOC` en priorité.

`use_parent_child` pose **deux** clés (`parent_child.use_parent_child` et
`ext.children_delimiter`) — fait vérifié en test de bout en bout (Phase 13) :
la première seule est un no-op silencieux sur le chemin document de cette
version du moteur, cf. le docstring de `chunking_profile.py`.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from pydantic import ValidationError

from app.domain.schemas.document import AdvancedChunkMethod, PrecheckDecision
from app.ingestion.chunking_profile import (
    CHILDREN_DELIMITER_KEY,
    DEFAULT_CHILDREN_DELIMITER,
    DOCLING_LAYOUT_RECOGNIZE,
    DOCLING_SINGLE_TASK_PAGE_FALLBACK,
    DOCLING_SINGLE_TASK_PAGE_MARGIN,
    HEAVY_OCR_LAYOUT_RECOGNIZE,
    LAYOUT_RECOGNIZE_KEY,
    NO_CHUNK_METHOD,
    PARENT_CHILD_EXT_KEY,
    PARENT_CHILD_KEY,
    PLAIN_TEXT_LAYOUT_RECOGNIZE,
    TABLE_CHUNK_METHOD,
    TASK_PAGE_SIZE_KEY,
    profile_for_decision,
)
from app.ingestion.precheck.schemas import PrecheckFormat
from app.providers.engine.base import CHUNK_METHODS, DEFAULT_CHUNK_METHOD

_PARENT_CHILD_PARSER_CONFIG = {
    PARENT_CHILD_KEY: {"use_parent_child": True},
    PARENT_CHILD_EXT_KEY: {CHILDREN_DELIMITER_KEY: DEFAULT_CHILDREN_DELIMITER},
}


def test_a_clean_document_gets_plain_text_by_default() -> None:
    """`is_likely_scanned=False`, aucune option : nouveau défaut, `Plain Text`."""
    profile = profile_for_decision(
        PrecheckDecision(), document_format=PrecheckFormat.PDF, is_likely_scanned=False
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: PLAIN_TEXT_LAYOUT_RECOGNIZE}


def test_a_scanned_document_gets_deep_doc_by_default() -> None:
    """`is_likely_scanned=True`, aucune option : `DeepDOC`, posé explicitement."""
    profile = profile_for_decision(
        PrecheckDecision(), document_format=PrecheckFormat.PDF, is_likely_scanned=True
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


def test_heavy_ocr_overrides_a_clean_document() -> None:
    """« OCR lourd » garde la priorité même sur un document jugé propre au precheck."""
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


def test_heavy_ocr_is_a_no_op_on_an_already_scanned_document() -> None:
    """« OCR lourd » et un document déjà jugé scanné convergent vers le même `DeepDOC`."""
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=True,
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


def test_optimizing_for_tables_on_a_spreadsheet_selects_the_table_template() -> None:
    """« Optimiser pour les tableaux » sur un tableur : un chunk = une ligne de données.

    Chemin inchangé par ADR-023 (hors mandat) : `is_likely_scanned` n'a pas de
    sens sur un tableur (toujours `False` côté precheck) et n'y produit aucun
    effet, quelle que soit sa valeur.
    """
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=True),
        document_format=PrecheckFormat.SPREADSHEET,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == TABLE_CHUNK_METHOD
    assert profile.parser_config == {}


def test_optimizing_for_tables_on_a_pdf_reinforces_the_layout_instead() -> None:
    """Sur un PDF, `chunk_method=table` échoue côté moteur : on renforce la mise en page.

    DeepDOC (`naive`) reconnaît déjà les tableaux dans un PDF ; c'est le même
    levier que `heavy_ocr`, pas un template séparé qui n'existe pas pour ce format.
    Garde la priorité sur le nouveau défaut même si le document est jugé propre.
    """
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


def test_heavy_ocr_reinforces_the_layout_recognition_without_changing_the_template() -> None:
    """Le moteur n'a qu'un extracteur visuel : l'OCR lourd se règle, il ne se choisit pas."""
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True), document_format=PrecheckFormat.PDF, is_likely_scanned=True
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


def test_both_options_are_cumulative_on_a_spreadsheet() -> None:
    """Un tableau scanné coche les deux : le profil doit porter les deux effets."""
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=True, heavy_ocr=True),
        document_format=PrecheckFormat.SPREADSHEET,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == TABLE_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


@pytest.mark.parametrize(
    ("optimize_for_tables", "heavy_ocr", "is_likely_scanned"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, False),
        (False, False, True),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ],
)
def test_an_image_never_carries_a_template_whatever_the_options(
    optimize_for_tables: bool, heavy_ocr: bool, is_likely_scanned: bool
) -> None:
    """Sur une image, le seul profil que le moteur accepte est le profil vide.

    Il pose `picture` lui-même au téléversement puis refuse tout `chunk_method`
    sur un document visuel — `Not supported yet!`, document en échec, zéro
    fragment (fait vérifié de bout en bout, y compris avec `picture` comme
    valeur). Les options — dont désormais `is_likely_scanned` — n'ont par
    ailleurs aucun effet observable : le parseur d'image OCRise de toute façon.
    """
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=optimize_for_tables, heavy_ocr=heavy_ocr),
        document_format=PrecheckFormat.IMAGE,
        is_likely_scanned=is_likely_scanned,
    )

    assert profile.chunk_method is NO_CHUNK_METHOD
    assert profile.parser_config == {}


@pytest.mark.parametrize(
    ("optimize_for_tables", "heavy_ocr", "use_parent_child", "is_likely_scanned"),
    [
        (False, False, False, False),
        (True, False, False, False),
        (False, True, False, False),
        (False, False, True, False),
        (True, True, True, False),
        (False, False, False, True),
        (True, True, True, True),
    ],
)
def test_a_presentation_never_carries_a_template_whatever_the_options(
    optimize_for_tables: bool,
    heavy_ocr: bool,
    use_parent_child: bool,
    is_likely_scanned: bool,
) -> None:
    """Sur un diaporama, le seul profil que le moteur accepte est le profil vide.

    Même garde moteur que l'image, et elle ne regarde pas la valeur envoyée :
    `validate_chunk_method` refuse `Not supported yet!` dès que la requête porte
    une clé `chunk_method` **et** que le nom finit par `.ppt`/`.pptx`/`.pages`.
    Poser `naive` — ce que faisait la production — faisait donc échouer le
    document 31 du corpus avant tout fragment, là où ne rien poser en rend 4.
    Le `parser_config` reste vide lui aussi : `rag/app/presentation.py` ne le
    lit que sur la branche `.pdf`.
    """
    profile = profile_for_decision(
        PrecheckDecision(
            optimize_for_tables=optimize_for_tables,
            heavy_ocr=heavy_ocr,
            use_parent_child=use_parent_child,
        ),
        document_format=PrecheckFormat.PRESENTATION,
        is_likely_scanned=is_likely_scanned,
    )

    assert profile.chunk_method is NO_CHUNK_METHOD
    assert profile.parser_config == {}


@pytest.mark.parametrize(
    ("optimize_for_tables", "heavy_ocr", "use_parent_child", "is_likely_scanned"),
    [
        (False, False, False, False),
        (True, False, False, False),
        (False, True, False, False),
        (False, False, True, False),
        (True, True, True, False),
        (False, False, False, True),
        (True, True, True, True),
    ],
)
def test_an_eml_never_carries_a_template_whatever_the_options(
    optimize_for_tables: bool,
    heavy_ocr: bool,
    use_parent_child: bool,
    is_likely_scanned: bool,
) -> None:
    """Sur un `.eml`, le seul profil que le moteur accepte est le profil vide (ADR-044 §5.4).

    Mesuré en direct : poser `naive` échoue avec le même message que le
    diaporama (« file type not supported yet(pdf, xlsx, doc, docx, txt
    supported) »), ne rien poser laisse le moteur choisir `email` et rend 2
    fragments propres. `.eml` n'a pas de `PrecheckFormat` dédié — c'est le
    nom de fichier qui déclenche ce court-circuit, pas `document_format`
    (ici `UNSUPPORTED`, comme `.docx`/`.md`).
    """
    profile = profile_for_decision(
        PrecheckDecision(
            optimize_for_tables=optimize_for_tables,
            heavy_ocr=heavy_ocr,
            use_parent_child=use_parent_child,
        ),
        document_format=PrecheckFormat.UNSUPPORTED,
        is_likely_scanned=is_likely_scanned,
        filename="36-email-with-attachment.eml",
    )

    assert profile.chunk_method is NO_CHUNK_METHOD
    assert profile.parser_config == {}


@pytest.mark.parametrize("filename", ["36-email-with-attachment.EML", "rapport.Eml"])
def test_an_eml_short_circuit_is_case_insensitive(filename: str) -> None:
    """L'extension se compare en minuscules : un `.EML`/`.Eml` téléversé reste reconnu."""
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=False, heavy_ocr=False, use_parent_child=False),
        document_format=PrecheckFormat.UNSUPPORTED,
        is_likely_scanned=False,
        filename=filename,
    )

    assert profile.chunk_method is NO_CHUNK_METHOD


def test_an_unsupported_document_without_the_eml_extension_is_unaffected() -> None:
    """Le court-circuit ne s'étend pas à tout `UNSUPPORTED` — seul `.eml` a cette contrainte.

    Un `.docx`/`.md` classé `UNSUPPORTED` n'a jamais eu ce problème (mesuré,
    ADR-044 §5.4) : il doit continuer à recevoir le routage par défaut
    (`naive`), pas hériter du profil vide réservé à `.eml`.
    """
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=False, heavy_ocr=False, use_parent_child=False),
        document_format=PrecheckFormat.UNSUPPORTED,
        is_likely_scanned=False,
        filename="rapport.docx",
    )

    assert profile.chunk_method is DEFAULT_CHUNK_METHOD


def test_an_eml_filename_defaults_to_the_unaffected_path_when_omitted() -> None:
    """`filename` non fourni (défaut `""`) ne déclenche jamais le court-circuit.

    Garantit qu'un appelant existant qui ne passe pas ce nouveau paramètre
    garde exactement le comportement d'avant ce correctif.
    """
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=False, heavy_ocr=False, use_parent_child=False),
        document_format=PrecheckFormat.UNSUPPORTED,
        is_likely_scanned=False,
    )

    assert profile.chunk_method is DEFAULT_CHUNK_METHOD


@pytest.mark.parametrize(
    ("optimize_for_tables", "heavy_ocr", "document_format", "is_likely_scanned"),
    [
        (False, False, PrecheckFormat.PDF, False),
        (True, False, PrecheckFormat.PDF, False),
        (False, True, PrecheckFormat.PDF, False),
        (True, True, PrecheckFormat.PDF, False),
        (False, False, PrecheckFormat.PDF, True),
        (False, False, PrecheckFormat.SPREADSHEET, False),
        (True, False, PrecheckFormat.SPREADSHEET, False),
        (False, True, PrecheckFormat.SPREADSHEET, False),
        (True, True, PrecheckFormat.SPREADSHEET, False),
        (True, False, PrecheckFormat.UNSUPPORTED, False),
    ],
)
def test_every_profile_uses_a_template_the_engine_knows(
    optimize_for_tables: bool,
    heavy_ocr: bool,
    document_format: PrecheckFormat,
    is_likely_scanned: bool,
) -> None:
    """Aucune combinaison ne peut produire un template hors du catalogue réel."""
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=optimize_for_tables, heavy_ocr=heavy_ocr),
        document_format=document_format,
        is_likely_scanned=is_likely_scanned,
    )

    assert profile.chunk_method in CHUNK_METHODS


def test_parent_child_poses_both_the_declared_and_the_effective_key() -> None:
    """`use_parent_child` s'ajoute au routage `layout_recognize` sans le changer.

    Deux clés, pas une seule (fait vérifié en test de bout en bout, Phase 13) :
    `parent_child.use_parent_child` seul seule est un no-op silencieux sur le
    chemin document de cette version du moteur — `ext.children_delimiter` est
    la clé que `rag/app/naive.py::chunk()` lit réellement.
    """
    profile = profile_for_decision(
        PrecheckDecision(use_parent_child=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {
        LAYOUT_RECOGNIZE_KEY: PLAIN_TEXT_LAYOUT_RECOGNIZE,
        **_PARENT_CHILD_PARSER_CONFIG,
    }


def test_parent_child_is_cumulable_with_heavy_ocr() -> None:
    """`use_parent_child` reste indépendant et cumulable avec les options historiques."""
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True, use_parent_child=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {
        LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE,
        **_PARENT_CHILD_PARSER_CONFIG,
    }


def test_parent_child_is_additive_on_a_spreadsheet_table_template() -> None:
    """Le template tabulaire (chemin Étape 5, hors mandat) reste additif lui aussi."""
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=True, use_parent_child=True),
        document_format=PrecheckFormat.SPREADSHEET,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == TABLE_CHUNK_METHOD
    assert profile.parser_config == _PARENT_CHILD_PARSER_CONFIG


@pytest.mark.parametrize(
    ("use_parent_child", "is_likely_scanned"),
    [
        (True, False),
        (True, True),
    ],
)
def test_an_image_never_carries_parent_child_either(
    use_parent_child: bool, is_likely_scanned: bool
) -> None:
    """Même court-circuit que les options historiques (§4 du module) : profil vide."""
    profile = profile_for_decision(
        PrecheckDecision(use_parent_child=use_parent_child),
        document_format=PrecheckFormat.IMAGE,
        is_likely_scanned=is_likely_scanned,
    )

    assert profile.chunk_method is NO_CHUNK_METHOD
    assert profile.parser_config == {}


# ─── ADR-043 — Docling sur le cas colonnes **et** tableaux ────────────────────


class _ColumnsAndTablesSignals(TypedDict):
    """Type précis de `_COLUMNS_AND_TABLES` : sans lui, `**dict[str, int]` fait

    craindre à mypy que la clé `filename` (`str`, ajoutée par le correctif
    `.eml`) puisse recevoir un `int` à chaque site d'appel qui déballe ce
    dict — un faux positif de la vérification `**kwargs`, pas un vrai risque
    (ce dict ne porte jamais `filename`).
    """

    two_column_page_count: int
    table_count: int
    page_count: int


_COLUMNS_AND_TABLES: _ColumnsAndTablesSignals = {
    "two_column_page_count": 3,
    "table_count": 16,
    "page_count": 40,
}
"""Les signaux de `real-notice-2042.pdf` au manifeste — le document de la mesure."""


def test_columns_and_tables_route_the_layout_analysis_to_docling() -> None:
    """Le cas qu'ADR-042 laissait à l'arbitrage humain, refermé par un troisième profil.

    Docling **remplace** `DeepDOC` sur cette branche, il ne s'y ajoute pas :
    l'utilisateur a coché « Analyser la mise en page », et c'est la même
    intention qui est servie — mieux, et sans régression de latence (325 s
    contre 361 s mesurées).
    """
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
        **_COLUMNS_AND_TABLES,
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config[LAYOUT_RECOGNIZE_KEY] == DOCLING_LAYOUT_RECOGNIZE


def test_docling_always_travels_with_a_single_task_page_size() -> None:
    """Sans cette clé, le moteur reconvertit le document entier à chaque tâche de 12 pages.

    Latence en O(pages²) (ADR-043, défaut n°1) : la taille de tâche doit
    dépasser le nombre de pages pour n'en produire qu'une seule.
    """
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
        **_COLUMNS_AND_TABLES,
    )

    task_page_size = profile.parser_config[TASK_PAGE_SIZE_KEY]
    assert task_page_size == 40 + DOCLING_SINGLE_TASK_PAGE_MARGIN
    assert isinstance(task_page_size, int)
    assert task_page_size > _COLUMNS_AND_TABLES["page_count"]


def test_an_unknown_page_count_falls_back_to_the_engine_ceiling() -> None:
    """Le défaut inverse serait une tâche par page — exactement le défaut à supprimer."""
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
        two_column_page_count=3,
        table_count=16,
        page_count=None,
    )

    assert profile.parser_config[TASK_PAGE_SIZE_KEY] == DOCLING_SINGLE_TASK_PAGE_FALLBACK


def test_a_scanned_document_never_reaches_docling() -> None:
    """Garde intouchable d'ADR-023 §3, prioritaire sur le raffinement d'ADR-043.

    Un scan est routé vers `DeepDOC` même quand il porte les deux signaux :
    l'OCR de Docling est chargé sur un modèle de reconnaissance chinois
    (ADR-043 §Conséquences), et rien de cette campagne ne l'a mesuré sur un
    document numérisé.
    """
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=True,
        **_COLUMNS_AND_TABLES,
    )

    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}
    assert TASK_PAGE_SIZE_KEY not in profile.parser_config


def test_columns_and_tables_without_layout_analysis_stay_on_plain_text() -> None:
    """Décocher l'analyse de mise en page reste un choix respecté (ADR-042 inchangée).

    Docling raffine la branche « analyse complète » ; il ne se substitue jamais
    au mode direct, dix fois plus rapide, que l'utilisateur a demandé.
    """
    profile = profile_for_decision(
        PrecheckDecision(),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
        **_COLUMNS_AND_TABLES,
    )

    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: PLAIN_TEXT_LAYOUT_RECOGNIZE}


@pytest.mark.parametrize(
    ("two_column_page_count", "table_count", "why"),
    [
        (0, 16, "sans colonnes, aucun grief d'ordre de lecture à corriger"),
        (3, 0, "sans tableau, `Plain Text` fait aussi bien pour dix fois moins cher"),
        (0, 0, "aucun des deux signaux : le cas ordinaire d'ADR-023"),
    ],
)
def test_one_signal_alone_never_routes_to_docling(
    two_column_page_count: int, table_count: int, why: str
) -> None:
    """Les deux signaux sont exigés **ensemble** — c'est le seul cas mesuré."""
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
        two_column_page_count=two_column_page_count,
        table_count=table_count,
        page_count=40,
    )

    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}, why


def test_optimize_for_tables_alone_also_reaches_docling_on_that_case() -> None:
    """La branche est celle de `force_layout_analysis`, pas celle d'une seule case.

    `optimize_for_tables` sur un PDF est déjà exactement `heavy_ocr`
    (`chunking_profile.py`, cas vérifié en bout en bout) : le raffinement suit
    la branche, il ne privilégie pas l'une des deux cases.
    """
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
        **_COLUMNS_AND_TABLES,
    )

    assert profile.parser_config[LAYOUT_RECOGNIZE_KEY] == DOCLING_LAYOUT_RECOGNIZE


def test_docling_stays_cumulative_with_parent_child() -> None:
    """Le raffinement ne casse pas l'additivité de la Phase 13."""
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True, use_parent_child=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
        **_COLUMNS_AND_TABLES,
    )

    assert profile.parser_config == {
        LAYOUT_RECOGNIZE_KEY: DOCLING_LAYOUT_RECOGNIZE,
        TASK_PAGE_SIZE_KEY: 41,
        **_PARENT_CHILD_PARSER_CONFIG,
    }


@pytest.mark.parametrize(
    "document_format",
    [
        PrecheckFormat.SPREADSHEET,
        PrecheckFormat.IMAGE,
        PrecheckFormat.PRESENTATION,
        PrecheckFormat.UNSUPPORTED,
    ],
)
def test_only_a_pdf_can_be_routed_to_docling(document_format: PrecheckFormat) -> None:
    """Le contrôle de format est redondant avec le precheck, et écrit quand même.

    `two_column_page_count` est toujours nul hors PDF (`precheck/columns.py`) :
    ce test verrouille que ce routage ne dépend pas de cette propriété-là pour
    rester borné au format sur lequel il a été mesuré.
    """
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=document_format,
        is_likely_scanned=False,
        **_COLUMNS_AND_TABLES,
    )

    assert profile.parser_config.get(LAYOUT_RECOGNIZE_KEY) != DOCLING_LAYOUT_RECOGNIZE
    assert TASK_PAGE_SIZE_KEY not in profile.parser_config


def test_the_new_signals_default_to_the_behaviour_that_predates_adr_043() -> None:
    """Un appelant qui ne passe pas les signaux obtient exactement l'ancien profil.

    C'est la propriété de non-régression du raffinement : les trois arguments
    ajoutés ont un défaut neutre, et aucun chemin existant n'a eu à changer
    pour continuer à produire ce qu'il produisait.
    """
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
    )

    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


# ─── Phase 15 — sélection fine du template (catalogue fermé) ─────────────────


@pytest.mark.parametrize("override", list(AdvancedChunkMethod))
def test_the_override_replaces_the_template_chosen_by_the_automatic_routing(
    override: AdvancedChunkMethod,
) -> None:
    """Posé, il gagne sur le routage automatique — c'est tout ce qu'il fait."""
    profile = profile_for_decision(
        PrecheckDecision(chunk_method_override=override),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == override.value
    assert profile.chunk_method in CHUNK_METHODS


def test_the_override_replaces_the_table_template_on_a_spreadsheet_too() -> None:
    """La branche tableur n'est pas une exception : elle aussi part du routage automatique.

    C'est le seul chemin par lequel un utilisateur averti peut retrouver le
    libellé de feuille que `table` perd sur un `.xlsx` multi-feuilles
    (§ docstring de module) — sans que RAGEngine ait à en décider pour lui.
    """
    profile = profile_for_decision(
        PrecheckDecision(optimize_for_tables=True, chunk_method_override=AdvancedChunkMethod.NAIVE),
        document_format=PrecheckFormat.SPREADSHEET,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {}


def test_the_override_never_touches_the_layout_recognition() -> None:
    """Substitutif sur le template, transparent sur le reste : les deux restent cumulables."""
    forced = profile_for_decision(
        PrecheckDecision(heavy_ocr=True, chunk_method_override=AdvancedChunkMethod.LAWS),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
    )
    untouched = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
    )

    assert forced.chunk_method == "laws"
    assert forced.parser_config == untouched.parser_config


def test_the_override_stays_cumulative_with_parent_child_and_docling() -> None:
    """Aucune des deux couches additives n'est perdue quand un template est forcé."""
    profile = profile_for_decision(
        PrecheckDecision(
            heavy_ocr=True,
            use_parent_child=True,
            chunk_method_override=AdvancedChunkMethod.PAPER,
        ),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
        **_COLUMNS_AND_TABLES,
    )

    assert profile.chunk_method == "paper"
    assert profile.parser_config == {
        LAYOUT_RECOGNIZE_KEY: DOCLING_LAYOUT_RECOGNIZE,
        TASK_PAGE_SIZE_KEY: 41,
        **_PARENT_CHILD_PARSER_CONFIG,
    }


@pytest.mark.parametrize("document_format", [PrecheckFormat.IMAGE, PrecheckFormat.PRESENTATION])
def test_the_override_is_ignored_where_the_engine_refuses_any_template(
    document_format: PrecheckFormat,
) -> None:
    """Honorer la consigne y ferait échouer le document — l'ignorer l'indexe.

    Les deux formats rendent leur profil vide avant même de lire la sélection
    fine : c'est le seul profil que la garde `validate_chunk_method` accepte,
    et l'interface ne propose de toute façon jamais le sélecteur ici.
    """
    profile = profile_for_decision(
        PrecheckDecision(chunk_method_override=AdvancedChunkMethod.LAWS),
        document_format=document_format,
        is_likely_scanned=False,
    )

    assert profile.chunk_method is NO_CHUNK_METHOD
    assert profile.parser_config == {}


def test_an_absent_override_reproduces_the_routing_that_predates_phase_15() -> None:
    """Défaut neutre : le champ n'existe pas pour un client qui ne l'envoie pas."""
    profile = profile_for_decision(
        PrecheckDecision(heavy_ocr=True),
        document_format=PrecheckFormat.PDF,
        is_likely_scanned=False,
    )

    assert profile.chunk_method == DEFAULT_CHUNK_METHOD
    assert profile.parser_config == {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE}


@pytest.mark.parametrize(
    "refused", ["email", "book", "one", "tag", "resume", "picture", "presentation", "table"]
)
def test_the_override_catalogue_is_closed_to_the_measured_subset(refused: str) -> None:
    """Un template hors catalogue est refusé à la porte, pas accepté puis subi.

    Tous ceux listés ici sont pourtant des valeurs **valides pour le moteur**
    (`CHUNK_METHODS`) : c'est bien RAGEngine qui restreint, et le refus se
    produit à la validation du schéma, avant tout chunking.

    `email` en particulier : il **n'échoue pas** sur un PDF, il indexe les
    octets bruts du fichier en rendant `run=done` — 2,2 % de couverture source
    mesurée, premier fragment `%PDF-1.4 …`. C'est exactement le genre de succès
    apparent qu'un catalogue ouvert laisserait passer.
    """
    assert refused in CHUNK_METHODS, "Le test ne vaut que sur des valeurs que le moteur accepte."

    with pytest.raises(ValidationError):
        PrecheckDecision(chunk_method_override=refused)  # type: ignore[arg-type]  # test du refus


def test_the_profile_is_immutable() -> None:
    """Un profil partagé entre deux appels ne doit pas pouvoir muter sous leurs pieds."""
    profile = profile_for_decision(
        PrecheckDecision(), document_format=PrecheckFormat.PDF, is_likely_scanned=False
    )

    with pytest.raises(ValueError, match="frozen"):
        profile.chunk_method = "book"  # type: ignore[misc]  # test de l'immuabilité
