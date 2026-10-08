"""Schéma du rapport de precheck — contenu exact de `documents.precheck_report`.

Défini dans le domaine (ADR-070) : l'API en expose la forme (`DocumentRead.precheck_report`,
OpenAPI) comme le precheck (`app/ingestion/precheck/`, qui le ré-exporte) la produit — un seul
schéma, jamais deux copies qui divergent.

Le rapport est sérialisé tel quel (`model_dump(mode="json")`) dans la colonne
JSONB `Document.precheck_report` (`docs/domain-model.md` §2), et
`detected_languages` alimente en plus la colonne dédiée du même nom (ADR-003).

Chaque champ existe pour l'une de ces deux raisons, jamais par anticipation :

1. **il est montré à l'utilisateur** sur l'écran d'aiguillage (CONTRIBUTING.md §1 :
   « nombre de tableaux identifiés, présence de schémas ou de texte scanné ») ;
2. **il alimente le mapping decision → `ChunkingProfile`**
   (`docs/ingestion-engine-architecture.md` §4) : `table_count > 0` motive
   l'option « Optimiser pour les tableaux », `is_likely_scanned` arbitre
   `layout_recognize` en l'absence de décision explicite (ADR-023).

Le rapport est un **signal d'aide à la décision, pas une validation** : un
format inconnu ou un fichier illisible produit un rapport dégradé
(`supported = False` et/ou `error` renseigné), jamais une exception.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)


class PrecheckFormat(StrEnum):
    """Famille de format reconnue par l'analyse préalable."""

    PDF = "pdf"
    """PDF — le cas le plus riche : texte, scan, tableaux, figures."""

    SPREADSHEET = "spreadsheet"
    """Tableur (`.xlsx`, `.xlsm`, `.csv`) — tableaux par construction."""

    IMAGE = "image"
    """Image seule (`.png`, `.jpg`, `.tiff`…) — reconnue, délibérément non analysée.

    Distinct d'`UNSUPPORTED` : RAGEngine sait exactement quoi en faire côté
    moteur (`chunk_method=None` — le moteur assigne `picture` lui-même et
    refuse qu'on le lui impose, `app/ingestion/chunking_profile.py`,
    `docs/ingestion-engine-architecture.md` §4), il ne prétend simplement pas la
    décrire en amont — lire son contenu exigerait un OCR local, exclu par
    CONTRIBUTING.md §2. Le rapport est donc **minimal mais valide** (`supported =
    True`, compteurs neutres), pas dégradé.
    """

    PRESENTATION = "presentation"
    """Diaporama (`.pptx`, `.ppt`, `.pages`) — reconnu, délibérément non analysé.

    Même statut que `IMAGE`, et pour la même raison : RAGEngine sait exactement
    quoi en faire côté moteur — **ne rien lui imposer** — sans prétendre décrire
    le fichier en amont. Le moteur assigne `presentation` de lui-même au
    téléversement puis **refuse tout `chunk_method`** sur ces trois extensions,
    quelle qu'en soit la valeur, y compris la sienne (source lue dans l'instance
    déployée : `api/utils/validation_utils.py::validate_chunk_method`, garde
    `re.search(r"\\.(ppt|pptx|pages)$", doc.name)`). Cette famille existe donc
    pour que `app/ingestion/chunking_profile.py` puisse reconnaître le cas — le
    classer `UNSUPPORTED` le noyait avec `.docx`/`.md`, qui n'ont pas cette
    contrainte.

    `.eml` **a** la même contrainte (mesuré, ADR-044 §5.4) mais reste classé
    `UNSUPPORTED` plutôt que promu ici : une seule extension, reconnue par nom
    de fichier directement dans `chunking_profile.py::profile_for_decision`
    plutôt que par une valeur d'énumération dédiée — corrigé une fois qu'un
    vrai bug l'a exigé, pas anticipé pour une seule occurrence.

    Les trois extensions sont exactement celles de la garde du moteur, pas une
    liste maison : `.pages` n'est pas téléversable aujourd'hui
    (`Settings.upload_allowed_content_types`) et figure ici quand même, pour que
    l'ajouter un jour à la liste d'admission ne fasse pas ressurgir le rejet.

    Le rapport est **minimal mais valide** (`supported = True`, compteurs
    neutres) : contrairement à `IMAGE`, `is_likely_scanned` y reste `False` —
    un diaporama porte du texte natif, il n'a rien d'une numérisation.
    """

    UNSUPPORTED = "unsupported"
    """Format hors périmètre de l'analyse légère : rapport dégradé."""


class PrecheckErrorCode(StrEnum):
    """Cause d'un rapport dégradé, exploitable par l'interface pour l'expliquer."""

    UNSUPPORTED_FORMAT = "unsupported_format"
    """RAGEngine ne sait pas analyser ce format en amont du moteur d'ingestion."""

    CORRUPTED_FILE = "corrupted_file"
    """Le contenu ne correspond pas au format annoncé, ou le fichier est vide."""

    EXTRACTION_FAILED = "extraction_failed"
    """Le format est bon mais l'extraction a échoué (fichier tronqué, chiffré…)."""


class PrecheckError(BaseModel):
    """Indicateur d'échec porté par un rapport dégradé."""

    model_config = ConfigDict(frozen=True)

    code: PrecheckErrorCode = Field(description="Cause normalisée, stable pour l'interface.")
    message: str = Field(description="Explication lisible, sans détail d'implémentation.")


class PrecheckReport(BaseModel):
    """Résultat de l'analyse préalable d'un document.

    Toutes les valeurs comptables (`table_count`, `figure_count`,
    `text_character_count`) portent sur les **pages réellement analysées**, pas
    nécessairement sur tout le document : l'analyse est bornée pour rester
    rapide (voir `analyzed_page_count`).
    """

    model_config = ConfigDict(frozen=True)

    supported: bool = Field(
        description=(
            "La chaîne d'ingestion sait traiter ce format. `False` ⇒ rapport "
            "dégradé : les autres champs sont à leur valeur neutre et n'ont "
            "aucun sens. `True` n'implique pas pour autant un rapport riche : "
            "`format = image` est reconnu et routé correctement, mais délibérément "
            "non inspecté (cf. `PrecheckFormat.IMAGE`), donc lui aussi neutre."
        )
    )
    format: PrecheckFormat = Field(description="Famille de format retenue pour l'analyse.")
    size_bytes: int = Field(ge=0, description="Taille du fichier téléversé, en octets.")
    page_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Nombre de pages du document. `None` pour les formats sans pagination "
            "(tableur) ou quand l'extraction a échoué. Alimente `Document.page_count`."
        ),
    )
    analyzed_page_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Nombre de pages effectivement inspectées. Inférieur à `page_count` sur "
            "un document volumineux (échantillonnage borné) : l'interface doit dire "
            "« sur N pages analysées » plutôt que d'affirmer un total."
        ),
    )
    text_character_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Caractères non blancs extractibles sur les pages analysées. C'est la "
            "preuve chiffrée derrière `is_likely_scanned`."
        ),
    )
    is_likely_scanned: bool = Field(
        default=False,
        description=(
            "Le document est probablement une numérisation sans couche texte. "
            "**Garde-fou de routage** : `True` impose l'analyse de mise en page "
            "complète du moteur quelle que soit la décision reçue (ADR-023) — un "
            "scan ne peut pas être condamné au mode `Plain Text`, qui n'y "
            "produirait rien. `heavy_ocr` n'y est donc plus un choix mais un "
            "constat, et l'écran d'aiguillage le dit (ADR-031)."
        ),
    )
    scanned_page_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Part des pages analysées jugées numérisées. `None` quand la notion ne "
            "s'applique pas (tableur, aucune page analysée). Affiché tel quel pour "
            "que l'utilisateur puisse contredire l'heuristique en connaissance de cause."
        ),
    )
    table_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Tableaux détectés : structures réglées pour un PDF, feuilles non vides "
            "pour un tableur. Motive l'option « Optimiser pour les tableaux »."
        ),
    )
    figure_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Images incrustées sur les pages analysées (« présence de schémas », "
            "CONTRIBUTING.md §1). Sur une page numérisée, l'image de la page compte pour "
            "une figure : lire `is_likely_scanned` d'abord."
        ),
    )
    two_column_page_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Pages analysées portant **deux sections de texte côte à côte**. Toujours "
            "0 hors PDF. Signal d'aiguillage, pas de qualité : mesuré (ADR-042), "
            "l'analyse de mise en page complète du moteur y rend le texte ligne par "
            "ligne en traversant les colonnes, là où le mode direct respecte les "
            "colonnes. À lire **avec** `table_count` : un document qui porte les deux "
            "signaux oppose deux gains — l'ordre de lecture et la reconnaissance des "
            "tableaux — et l'arbitrage revient à l'utilisateur, pas au moteur."
        ),
    )
    detected_languages: list[str] = Field(
        default_factory=list,
        description=(
            "Codes ISO 639-1 détectés, par ordre de couverture décroissante "
            "(ADR-003). Alimente `Document.detected_languages`. Vide si le document "
            "ne contient pas assez de texte pour trancher."
        ),
    )
    error: PrecheckError | None = Field(
        default=None,
        description="Renseigné dès que le rapport est dégradé ; `None` en cas nominal.",
    )


class PrecheckReportRead(PrecheckReport):
    """Le rapport tel que l'API le **relit** : restitué à l'identique de ce qui est stocké.

    `documents.precheck_report` est du JSONB écrit par des versions successives du precheck
    (`two_column_page_count`, par exemple, n'existe pas dans les rapports anciens). Servir le
    modèle complet ajouterait des clés par défaut à ces rapports : le contrat de `/api/v1`
    ne doit jamais muter en silence (CONTRIBUTING.md §4). Cette variante ne restitue donc que les
    clés présentes dans le JSON relu, dans leur ordre d'origine — octet pour octet ce que
    servait l'API quand le champ était un dictionnaire libre (ADR-070).

    La *validation* reste celle de `PrecheckReport` : un rapport relu est vérifié contre le
    schéma, et c'est ce schéma que publie l'OpenAPI. Le producteur, lui, écrit toujours
    `PrecheckReport` (modèle complet).
    """

    _stored_keys: tuple[str, ...] | None = None

    @model_validator(mode="wrap")
    @classmethod
    def _remember_the_stored_keys(
        cls, data: object, handler: ModelWrapValidatorHandler[PrecheckReportRead]
    ) -> PrecheckReportRead:
        report = handler(data)
        if isinstance(data, Mapping):
            report._stored_keys = tuple(data)
        return report

    # Pas d'annotation de retour, volontairement : `-> dict[str, Any]` ferait publier à l'OpenAPI
    # un objet quelconque au lieu des champs du rapport.
    @model_serializer(mode="wrap")
    def _serialize_what_was_stored(  # type: ignore[no-untyped-def]  # noqa: ANN202
        self, handler: SerializerFunctionWrapHandler
    ):
        full: dict[str, Any] = handler(self)
        if self._stored_keys is None:
            return full
        return {key: full[key] for key in self._stored_keys if key in full}
