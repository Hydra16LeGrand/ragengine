"""Schémas des documents et de l'aiguillage human-in-the-loop du precheck.

`status` est en lecture seule dans le contrat d'API : il projette l'état du job
courant et n'a qu'un seul écrivain, le service de transition d'état.

**Il n'existe pas de schéma d'entrée pour l'ingestion** (ADR-014) :
`POST …/documents/ingest` reçoit un `multipart/form-data`, et `content_type`,
`size_bytes`, `checksum_sha256` et `storage_uri` sont **calculés côté serveur** à
partir des octets reçus. Aucune de ces quatre valeurs n'est plus acceptée du
client : l'idempotence par empreinte n'aurait aucun sens si l'empreinte était
déclarée par celui qui téléverse.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator

from app.domain.enums import DocumentStatus
from app.domain.schemas.common import MAX_THEME_LENGTH, InputModel, ReadModel, ResourceName
from app.domain.schemas.precheck import PrecheckReportRead


class AdvancedChunkMethod(StrEnum):
    """Templates de découpage ouverts à la **sélection fine** — catalogue fermé.

    Sous-ensemble strict des 13 templates de RAGFlow (`CHUNK_METHODS`), et ce
    n'est pas de la prudence décorative : chaque valeur ci-dessous a une mesure
    favorable derrière elle (Phase 15, Étape 2,
    `chunk-method-by-family-20260826` (mesure interne)), et chaque
    valeur absente a une raison de l'être. Exposer les 13 reviendrait à
    proposer des options dont on sait qu'elles échouent.

    **Ce qui n'est pas là, et pourquoi** :

    - `table` — déjà posé automatiquement sur un tableur par
      `optimize_for_tables` ; le proposer une seconde fois ne changerait rien.
    - `presentation` — routage automatique corrigé (§ `chunking_profile.py`),
      pas un choix : le moteur refuse qu'on le lui impose.
    - `picture` — même refus, sur une image.
    - `email` — **écarté sur défaut mesuré**, pas par manque de mesure : le
      parseur n'a aucune garde de format. Posé sur un PDF, il « réussit »
      (`run=done`, 2 fragments) en indexant les octets bruts du fichier —
      2,2 % de couverture du texte source, le premier fragment commençant par
      `%PDF-1.4 … /BaseFont /Helvetica`. Un succès apparent qui indexe du
      binaire est pire qu'un échec.
    - `book`, `one`, `tag`, `resume` — aucune mesure favorable. `book` éclate
      un article de 21 pages en 1044 fragments de 65 caractères médians (contre
      32 pour `paper`), les trois autres n'ont jamais rien montré.

    Le **filtrage par document** ne vit pas ici mais dans l'interface
    (`frontend/src/api/precheck.ts`) : ce catalogue dit ce qui est *permis*,
    l'écran d'aiguillage dit ce qui est *pertinent* pour le document analysé.
    Un client REST qui force `qa` sur un PDF obtient donc ce qu'il a demandé —
    et l'échec moteur mesuré qui va avec (« Unable to recognize Q&A
    structure. »). C'est un choix : une option avancée explicite n'est pas une
    suggestion, et le contrat d'API ne peut pas dépendre d'un rapport de
    precheck que l'appelant n'a pas forcément lu.
    """

    NAIVE = "naive"
    """Le défaut, nommable explicitement — aucun effet de bord, aucune surprise."""

    LAWS = "laws"
    """Découpage par article/section sur PDF structuré. Mesuré sur le RGPD réel (88 p) :
    545 fragments de 429 caractères médians contre 248 de 1773 pour `naive`, à couverture
    source égale (0,888 contre 0,892) et rappel croisé de 1,0 dans les deux sens.
    **S'effondre sur une mise en page à colonnes** — 19,2 % de fragments dégénérés
    mesurés : c'est pourquoi l'interface ne le propose jamais si le precheck a vu
    des colonnes."""

    PAPER = "paper"
    """Découpage par section d'article scientifique. Mesuré sur un article ACL réel
    (21 p, 2 colonnes, 11 tableaux) : 32 fragments de 1497 caractères médians contre
    44 de 1913 pour `naive`, même couverture, rappel croisé 1,0."""

    MANUAL = "manual"
    """Découpage par section de manuel. Mesuré sur les deux mêmes PDF que `laws` et
    `paper`, avec des résultats intermédiaires et un rappel croisé de 1,0 — proposé
    aux côtés des deux autres parce qu'un document long peut relever de l'un ou de
    l'autre, ce qu'aucune heuristique ne tranche à la place de l'utilisateur."""

    QA = "qa"
    """Un fragment par paire question/réponse. **Ne fonctionne que sur du texte
    structuré par titres** : mesuré sur un Markdown à sections, il rend 4 fragments
    `Question: … Answer: …` exploitables ; sur un PDF de FAQ pourtant explicitement
    rédigé en « Q : / R : », le moteur échoue (« Unable to recognize Q&A
    structure. », 0 fragment). L'interface ne le propose donc que hors PDF."""


class PrecheckDecision(InputModel):
    """Aiguillage choisi par l'utilisateur à l'issue du precheck.

    **Catalogue fermé** (`docs/ingestion-engine-architecture.md` §4) : les
    seules options exposées en v1, celles dont l'effet sur RAGFlow a été vérifié
    contre l'instance réelle. Un champ inconnu produit un `422` plutôt que d'être
    accepté puis ignoré au moment du chunking — une option silencieusement sans
    effet est pire qu'une option refusée.

    Les quatre sont **indépendantes et cumulables** : `optimize_for_tables`
    choisit le template de découpage, `heavy_ocr` le niveau d'analyse de mise
    en page, `use_parent_child` est une fonctionnalité moteur additive
    (Phase 13, l'étude d'impact interne de la phase 13 candidat 4) qui ne change ni
    l'une ni l'autre, et `chunk_method_override` (Phase 15) **remplace** le
    template choisi par le routage automatique sans jamais toucher au reste.

    `chunk_method_override` est la seule à ne pas être un booléen, et la seule
    à porter son propre catalogue fermé (`AdvancedChunkMethod`) : les valeurs
    autorisées y sont un sous-ensemble mesuré des 13 templates du moteur, pas
    les 13.

    `use_parent_child` est **opt-in, pas une proposition du precheck** —
    contrairement à `optimize_for_tables`/`heavy_ocr`, rien dans le rapport de
    precheck ne permet de deviner si l'utilisateur la veut : toujours
    décochée par défaut, toujours un choix explicite.

    Les **défauts de ce schéma sont le contrat d'API v1**, pas la
    recommandation produit : l'interface pré-coche `heavy_ocr` sur tout PDF
    (ADR-031), mais un client REST qui omet le champ garde le comportement
    qu'il a toujours eu (`false` ⇒ routage par `is_likely_scanned`, ADR-023).
    Basculer ce défaut serait une mutation silencieuse de `/v1` (CONTRIBUTING.md §4).

    **GraphRAG `method="ner"` (candidat 1 de l'étude d'impact) n'est
    volontairement pas exposé** : vérifié contre l'instance réelle
    (`infiniflow/ragflow:v0.26.4`), aucune tâche `graphrag` n'est mise en file
    par le chemin document (`queue_tasks`, `api/db/services/task_service.py`
    du conteneur déployé) — seul `queue_per_doc_raptor_task` existe pour
    RAPTOR, aucun équivalent `queue_per_doc_graphrag_task`. Le seul
    déclencheur qui fonctionne est l'endpoint dataset-wide `POST
    /datasets/{id}/index?type=graph`, explicitement écarté (écrase
    silencieusement `Knowledgebase.parser_config`, l'étude d'impact interne
    de la phase 13). Poser `parser_config.graphrag.method="ner"` au chunking d'un
    document est donc un no-op silencieux sur cette version — catalogue
    fermé, une option sans effet vérifié n'est jamais exposée (§ ci-dessus).
    """

    optimize_for_tables: bool = Field(
        default=False,
        description=(
            "Découper le document ligne à ligne comme un tableau (`chunk_method=table`). "
            "Motivé par `precheck_report.table_count`. Réserve : le parseur tabulaire "
            "infère des types et peut réinterpréter une valeur textuelle courte ambiguë."
        ),
    )
    heavy_ocr: bool = Field(
        default=False,
        description=(
            "Imposer l'analyse de mise en page complète du moteur (`DeepDOC`) au lieu du "
            "court-circuit `Plain Text`. Gain mesuré sur un PDF à couche texte : ordre des "
            "fragments exploitable (0 % → 100 % de positions fiables) et fin des coupures "
            "en plein milieu de phrase (33-60 % → 0 %), à qualité de texte équivalente "
            "(ADR-030/031). Coût : ingestion ~3,8 fois plus lente, et l'écart croît avec "
            "le nombre de pages (facteur 16 mesuré sur 50 pages). **Sans objet sur un "
            "document "
            "numérisé** : `precheck_report.is_likely_scanned` impose `DeepDOC` de toute "
            "façon, cocher ou décocher n'y change rien. Le défaut reste `false` — contrat "
            "v1 inchangé ; c'est l'interface qui propose l'option cochée sur les PDF."
        ),
    )
    use_parent_child: bool = Field(
        default=False,
        description=(
            "Indexer en petits fragments (« enfants ») pour un matching de recherche plus "
            "précis, tout en agrégeant vers le fragment parent (plus large, plus de "
            "contexte) au moment du retrieval. `parser_config.parent_child.use_parent_child` "
            "(Phase 13, candidat 4) — sans effet sur les documents qui ne l'activent pas."
        ),
    )
    chunk_method_override: AdvancedChunkMethod | None = Field(
        default=None,
        description=(
            "Sélection fine du template de découpage, **catalogue fermé** "
            "(`AdvancedChunkMethod`) et réservée à qui sait ce qu'il fait : posée, elle "
            "remplace le template que le routage automatique aurait choisi ; absente "
            "(le défaut), rien ne change — le routage d'ADR-023/042/043 s'applique tel "
            "quel. Ne touche **jamais** `layout_recognize` : le moteur de mise en page "
            "reste arbitré par `heavy_ocr`/`is_likely_scanned` comme avant, et les deux "
            "réglages restent cumulables. **Sans effet sur une image ni un diaporama** : "
            "le moteur y refuse tout `chunk_method` (`chunking_profile.py`), et une "
            "option sans effet vérifié n'est jamais exposée (§ ci-dessus) — la poser "
            "sur ces formats est ignoré plutôt que de faire échouer le document."
        ),
    )


class DocumentUpdate(InputModel):
    """Mise à jour partielle d'un document — `PATCH /api/v1/documents/{document_id}`."""

    filename: ResourceName | None = None
    theme: str | None = Field(
        default=None,
        max_length=MAX_THEME_LENGTH,
        description=(
            "Étiquette de thème saisie par l'utilisateur, pour organiser une base "
            "par sujet. Contrairement à `detected_languages`, jamais déduite — "
            "`null` efface le thème. Filtrable au retrieval (`RetrievalFilters."
            "themes`), même sémantique descriptive que `languages`."
        ),
    )


class DocumentPrecheckDecisionUpdate(InputModel):
    """Aiguillage — `PUT /api/v1/documents/{document_id}/precheck/decision`.

    `chunk_method_override` y est refusé (422), désactivé pour cette version —
    la validation est posée ici plutôt que sur `PrecheckDecision` elle-même :
    `PrecheckDecision.model_validate` reconstruit aussi la décision d'un
    document **déjà ingéré** depuis son stockage (`workers/tasks/ingestion.py`,
    `profile_for_chunking`) pour une éventuelle reprise de job — un document
    ingéré avant cette désactivation, avec `chunk_method_override` déjà posé,
    doit rester rejouable tel quel. Seule la **soumission d'une nouvelle**
    décision est concernée.
    """

    decision: PrecheckDecision

    @field_validator("decision")
    @classmethod
    def _chunk_method_override_disabled_for_v1(cls, value: PrecheckDecision) -> PrecheckDecision:
        """Désactivé pour cette version (2026-09-06) — incident réel : `laws` sur un PDF

        réel a produit un fragment de 41 264 caractères (~8000 tokens), jamais mesuré
        aux limites du reranker en conditions réelles. Un lot de reranking contenant ce
        fragment a fait échouer `CUDA out of memory` (6,43 Gio pour un seul lot) — la
        mesure de Phase 15 (l'étude d'impact interne de la phase 13) portait sur la qualité du
        découpage, jamais sur son interaction avec le budget VRAM du reranking en aval.
        Décision explicite de l'utilisateur : retirer l'option tant qu'elle n'est pas
        éprouvée bout en bout. Rejet explicite (422), pas un ignorer silencieux — même
        philosophie que le catalogue fermé lui-même (cf. docstring de `PrecheckDecision`).
        """
        if value.chunk_method_override is not None:
            raise ValueError(
                "chunk_method_override est désactivé pour cette version : un "
                "découpage avancé (`laws`) a produit un fragment dépassant les "
                "limites du reranker en conditions réelles (incident du "
                "2026-09-06). Omettez ce champ — le routage automatique s'applique."
            )
        return value


class DocumentRead(ReadModel):
    """Document tel qu'exposé par l'API."""

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_uri: str
    status: DocumentStatus
    page_count: int | None = None
    detected_languages: list[str] = Field(
        default_factory=list, description="Codes ISO 639-1 détectés au precheck (ADR-003)."
    )
    theme: str | None = Field(
        default=None,
        description=(
            "Étiquette de thème saisie par l'utilisateur, pour organiser une base par sujet."
        ),
    )
    precheck_report: PrecheckReportRead | None = Field(
        default=None,
        description=(
            "Rapport d'analyse préalable (ADR-003, ADR-023), absent tant que le precheck "
            "n'a pas tourné. Les champs absents d'un rapport ancien le restent dans la "
            "réponse : ils ne sont jamais remplacés par une valeur par défaut."
        ),
    )
    precheck_decision: dict[str, Any] | None = None
    current_job_id: uuid.UUID | None = None
    ragflow_document_external_id: str | None = None
    has_quality_warning: bool = Field(
        default=False,
        description=(
            "Garde-fou qualité d'extraction (ADR-024) : `True` si le job courant a "
            "terminé avec un `JobWarning` (détail sur `GET /api/v1/jobs/{job_id}`). "
            "N'affecte jamais `status` — orthogonal à l'état du pipeline."
        ),
    )
    chunk_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Nombre total de fragments indexés côté moteur, calculé au même "
            "geste que le garde-fou qualité (ADR-024). `None` si le document a "
            "été ingéré avant l'introduction de ce champ, ou si le calcul a "
            "échoué (jamais bloquant) — pas de fragments détaillés sans ouvrir "
            "`GET /api/v1/documents/{document_id}/chunks`."
        ),
    )
    created_at: datetime
    updated_at: datetime


class DocumentChunkRead(ReadModel):
    """Un fragment indexé — `GET /api/v1/documents/{document_id}/chunks`.

    `page_start`/`page_end` sont dérivés d'`EngineChunk.page_range` (même
    filtre de rectangle non dégénéré qu'`EngineSearchResult` côté recherche) :
    la position brute en pixels n'a pas d'usage côté interface, seul le
    numéro de page compte.
    """

    chunk_id: str
    content: str
    page_start: int | None = Field(
        default=None, ge=1, description="Première page couverte par le fragment (1-based)."
    )
    page_end: int | None = Field(
        default=None, ge=1, description="Dernière page couverte — égale à `page_start` si unique."
    )
    important_keywords: list[str] = Field(default_factory=list)
    questions: list[str] = Field(
        default_factory=list, description="Questions générées si `auto_questions` était actif."
    )
    available: bool = Field(
        default=True,
        description=(
            "`False` si le fragment est exclu de la recherche côté moteur. Sur un "
            "document découpé en parent/enfant (`uses_parent_child`), ce sont les "
            "fragments **parents** : jamais interrogés directement, mais restitués "
            "à la place de leurs enfants dès qu'un enfant est retrouvé."
        ),
    )


class DocumentChunksMeta(ReadModel):
    """Métadonnées du document parent, rendues en tête de `DocumentChunksPage`."""

    chunk_method: str | None = Field(
        default=None, description="Template de chunking effectivement appliqué par le moteur."
    )
    chunk_count: int = Field(ge=0, description="Nombre total de fragments indexés.")
    token_count: int = Field(ge=0, description="Nombre total de tokens indexés.")
    uses_parent_child: bool = Field(
        default=False,
        description=(
            "`True` si le document a été indexé avec `use_parent_child`. Change la "
            "lecture de la liste : les fragments courts sont des **enfants** (unités "
            "de repérage), et ceux marqués `available=false` sont les **parents** "
            "réellement rendus par la recherche. Lu depuis la décision de precheck "
            "conservée en base, pas depuis le moteur — l'API de listage des fragments "
            "de RAGFlow n'expose pas le lien enfant→parent (`mom_id`)."
        ),
    )


class DocumentChunksPage(ReadModel):
    """Page de fragments — pagination réelle côté moteur (`page`/`page_size`), pas par curseur.

    Distincte de `Page[T]` (`app/domain/pagination.py`) : celle-ci pagine des
    lignes PostgreSQL par curseur opaque ; ici la source de vérité est RAGFlow,
    qui pagine nativement par `page`/`page_size` — reproduire un curseur
    par-dessus n'apporterait rien et masquerait `total`, utile pour l'UI.
    """

    data: list[DocumentChunkRead]
    document: DocumentChunksMeta
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
