"""Port `IngestionEngine` : contrat du moteur d'ingestion, d'indexation et de retrieval.

Référence normative : `docs/ingestion-engine-architecture.md` §2, elle-même issue
d'un audit empirique de RAGFlow v0.26.4 (instance réellement déployée et
interrogée). Les services et les tâches Celery n'importent que ce module : ni
RAGFlow, ni `httpx`, ni le moindre identifiant d'endpoint ne fuit au-delà de
`app/providers/engine/ragflow/` (CONTRIBUTING.md §3.3).

Trois traits de ce port ne sont pas des choix esthétiques mais des conséquences
directes de faits vérifiés — les retirer réintroduirait un piège connu :

- **`ensure_dataset`, pas `create_dataset`.** RAGFlow ne refuse pas un nom de
  dataset déjà pris : il renomme en silence (`audit-kb` → `audit-kb-renamed(1)`).
  Créer par nom lisible produirait un doublon à chaque reprise après panne.
- **`EngineParseStatus.inferred_phase` est `JobState | None`.** RAGFlow n'expose
  qu'un seul état discret (`run`) ; `chunking`/`embedding`/`indexing` restent
  notre abstraction, projetée par heuristique (§3). L'incertitude est dans le
  type, pas cachée sous une valeur par défaut.
- **`search(..., similarity_threshold: float | None)` sans défaut.** Activer le
  reranking change l'échelle du score combiné, qui peut passer sous le seuil et
  vider les résultats *sans erreur* (vérifié : rerank activé → `chunks: []`).
  C'est à l'appelant (service de retrieval, Phase 5) d'arbitrer les deux
  ensemble ; un défaut dans l'adaptateur reproduirait le piège en le masquant.
"""

from __future__ import annotations

from collections.abc import Sequence
from http import HTTPStatus
from typing import ClassVar, Final, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.core.errors import RAGEngineError
from app.domain.enums import JobState

CHUNK_METHODS: Final[frozenset[str]] = frozenset(
    {
        "naive",
        "book",
        "email",
        "laws",
        "manual",
        "one",
        "paper",
        "picture",
        "presentation",
        "qa",
        "table",
        "tag",
        "resume",
    }
)
"""Catalogue réel des templates de chunking RAGFlow (`docs/ingestion-engine-architecture.md` §4).

Treize valeurs, pas douze, et **jamais `general`** : liste confirmée par le
message de rejet de RAGFlow lui-même, pas par sa documentation. Seuls `naive`
(défaut) et `table` sont aiguillés par le precheck en v1 ; les autres restent
disponibles pour une sélection fine ultérieure.
"""

DEFAULT_CHUNK_METHOD: Final = "naive"
"""Template par défaut. DeepDOC applique OCR et analyse de mise en page même sur
un PDF porteur d'une couche texte (fait vérifié) : il n'existe pas de chemin
« texte simple » distinct à choisir."""

MAX_SEARCH_RESULTS: Final = 100
"""Borne du port sur `search(top_k=...)` — plafond réel de `page_size` côté
RAGFlow (`REST_API_MAX_PAGE_SIZE`), constaté en conditions réelles.

Définie ici plutôt que dans l'adaptateur : c'est une borne du **contrat**
`search()`, consommée par le schéma d'entrée du service de retrieval
(`app/domain/schemas/retrieval.py`), qui ne doit jamais importer depuis
`app/providers/engine/ragflow/` (CONTRIBUTING.md §3.3). RAGFlow étant le moteur
unique (ADR-001), l'exposer ici plutôt que de la dupliquer est défendable même
si sa valeur numérique reste un fait RAGFlow. `app/providers/engine/ragflow/
client.py` la ré-exporte telle quelle : au-delà, RAGFlow lève une `ValueError`
hors de son bloc de garde plutôt qu'une erreur métier propre.
"""


class EngineError(RAGEngineError):
    """Échec du moteur d'ingestion, traduit depuis le champ `code` de son corps JSON.

    Statut `502 Bad Gateway` : la faute est celle d'un tiers en amont, pas celle
    de la requête entrante. Aucune exception `httpx` ni aucun code RAGFlow brut
    ne franchit l'adaptateur (`docs/providers-architecture.md` §0).

    C'est aussi la traduction de `code=102` (« introuvable ou interdit »), que
    RAGFlow rend indiscernables : RAGEngine connaît l'existence de ses propres
    bases et documents par sa base PostgreSQL, l'ambiguïté du moteur ne doit
    jamais remonter jusqu'à l'utilisateur sous forme de 404 ou de 403 douteux.
    """

    status: ClassVar[int] = HTTPStatus.BAD_GATEWAY
    title: ClassVar[str] = "Bad Gateway"
    type: ClassVar[str] = "/problems/engine-error"


class EngineValidationError(EngineError):
    """Requête refusée par le moteur pour cause d'argument invalide (`code=101`).

    Permanente : la rejouer telle quelle échouerait à l'identique. Jamais
    éligible au retry Celery.
    """

    status: ClassVar[int] = HTTPStatus.UNPROCESSABLE_ENTITY
    title: ClassVar[str] = "Unprocessable Entity"
    type: ClassVar[str] = "/problems/engine-validation-error"


class EngineTransientError(EngineError):
    """Échec passager du moteur (`code` ∈ {100, 105, 106}) — et lui seul est rejouable.

    `100` (exception serveur générique), `105` (erreur de connexion) et `106`
    (traitement déjà en cours) décrivent un état momentané. Ce sont **les seules**
    erreurs que le retry Celery doit reprendre : élargir cet ensemble ferait
    boucler une tâche sur une faute permanente.
    """

    status: ClassVar[int] = HTTPStatus.SERVICE_UNAVAILABLE
    title: ClassVar[str] = "Service Unavailable"
    type: ClassVar[str] = "/problems/engine-transient-error"


class EngineDatasetRef(BaseModel):
    """Identifiant externe d'un dataset RAGFlow, opaque pour l'appelant."""

    model_config = ConfigDict(frozen=True)

    external_id: str = Field(
        min_length=1,
        description="Identifiant du dataset côté moteur, persisté en base par RAGEngine.",
    )


class EngineDocumentRef(BaseModel):
    """Identifiant externe d'un document côté moteur, opaque pour l'appelant."""

    model_config = ConfigDict(frozen=True)

    external_id: str = Field(min_length=1, description="Identifiant du document côté moteur.")


class ChunkingProfile(BaseModel):
    """Traduction d'une décision de precheck vers les paramètres RAGFlow (§4).

    `parser_config` reste volontairement un dictionnaire libre : sa forme change
    avec `chunk_method`, et la figer en modèle typé obligerait à suivre chaque
    évolution du moteur pour un gain nul côté domaine.
    """

    model_config = ConfigDict(frozen=True)

    chunk_method: str | None = Field(
        default=DEFAULT_CHUNK_METHOD,
        description=(
            "Template de chunking RAGFlow — une des valeurs de `CHUNK_METHODS`, ou "
            "`None` pour n'en imposer aucun et laisser le moteur appliquer celui "
            "qu'il déduit lui-même du type de fichier."
        ),
    )
    parser_config: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Paramètres du parseur (ex. `layout_recognize`), propres au template.",
    )

    @field_validator("chunk_method")
    @classmethod
    def _known_chunk_method(cls, value: str | None) -> str | None:
        """Rejette tout template absent du catalogue réel, `general` en tête.

        La validation est ici et non chez l'appelant : le moteur répondrait
        `code=101` en HTTP 200, une erreur bien plus coûteuse à diagnostiquer
        qu'un refus immédiat.

        `None` traverse : ce n'est pas un template inconnu mais l'absence
        assumée de consigne — voir la description du champ, et §4 pour le seul
        cas qui l'exige aujourd'hui (les images).
        """
        if value is None:
            return None
        if value not in CHUNK_METHODS:
            allowed = ", ".join(sorted(CHUNK_METHODS))
            message = f"`chunk_method` inconnu : « {value} ». Valeurs acceptées : {allowed}."
            raise ValueError(message)
        return value


class EngineParseStatus(BaseModel):
    """Projection de l'état RAGFlow — best-effort, cf. §3. PAS une source de vérité fine."""

    model_config = ConfigDict(frozen=True)

    run: Literal["unstart", "running", "cancel", "done", "fail"] = Field(
        description="Seul état discret réellement exposé par le moteur."
    )
    inferred_phase: JobState | None = Field(
        description=(
            "Phase projetée par heuristique (§3) : `chunking`, `embedding`, `indexing`, "
            "`completed` — ou `None` quand aucun marqueur connu n'apparaît. Jamais deviné."
        )
    )
    progress: float = Field(
        ge=-1.0,
        le=1.0,
        description=(
            "Avancement brut du moteur dans [-1.0, 1.0] — PAS un pourcentage, et bruité "
            "en file d'attente : signal de vivacité pour la veille (§5), rien de plus."
        ),
    )
    last_message: str | None = Field(
        description="Dernière ligne non vide de `progress_msg`, ou `None` si le journal est vide."
    )
    chunk_count: int = Field(ge=0, description="Nombre de fragments indexés à cet instant.")
    error: str | None = Field(
        description='Renseigné seulement lorsque `run == "fail"` ; `None` sinon.'
    )

    @property
    def is_terminal(self) -> bool:
        """`True` si le moteur ne fera plus évoluer ce document de lui-même."""
        return self.run in ("done", "fail", "cancel")


class EngineChunkPosition(BaseModel):
    """Un rectangle de position d'un fragment, tel que RAGFlow le rend (`positions`).

    Sous-modèle plutôt que des champs à plat sur `EngineChunk` : un fragment
    porte **plusieurs** rectangles (un par page couverte), `list[EngineChunk
    Position]` est donc la forme fidèle, pas un choix esthétique. Typé en
    `float`, comme `_RagflowChunk.positions` (`providers/engine/ragflow/
    client.py`) : RAGFlow ne garantit pas des coordonnées entières.
    """

    model_config = ConfigDict(frozen=True)

    page: float
    x0: float
    x1: float
    top: float
    bottom: float


class EngineChunk(BaseModel):
    """Un fragment brut d'un document déjà chunké, tel que RAGFlow le stocke.

    Distinct d'`EngineSearchResult` : celui-ci vient de `POST /retrieval` (un
    résultat de recherche, scoré) ; `EngineChunk` vient du listing direct des
    fragments d'un document (`list_document_chunks`), sans requête ni score —
    c'est la source brute utilisée par la génération de Q/R d'évaluation
    (Phase 8) et, désormais, par la consultation des fragments depuis
    l'interface (Phase 13).
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    content: str
    positions: list[EngineChunkPosition] = Field(default_factory=list)
    important_keywords: list[str] = Field(default_factory=list)
    questions: list[str] = Field(
        default_factory=list, description="Questions générées si `auto_questions` était actif."
    )
    available: bool = Field(
        default=True, description="`False` si le fragment a été désactivé côté moteur."
    )

    @property
    def page_range(self) -> tuple[int, int] | None:
        """Intervalle de pages fiable couvert par le fragment, ou `None`.

        Même filtre que `_page_range_of` (`providers/engine/ragflow/
        client.py`, côté recherche) : un rectangle réel vérifie `x0 < x1` et
        `top < bottom` ; les formats sans pagination (Word) rendent un rang de
        fragment déguisé en position, à `(0, 0, 0, 0)` ou assimilé, écarté ici.
        """
        pages = [
            int(position.page)
            for position in self.positions
            if position.x0 < position.x1 and position.top < position.bottom and position.page >= 1
        ]
        if not pages:
            return None
        return min(pages), max(pages)


class EngineDocumentChunksMeta(BaseModel):
    """Métadonnées du document portées par l'objet `doc` de `list_document_chunks`."""

    model_config = ConfigDict(frozen=True)

    chunk_method: str | None = Field(
        default=None, description="Template de chunking effectivement appliqué par le moteur."
    )
    chunk_count: int = Field(
        ge=0, description="Nombre total de fragments indexés pour ce document."
    )
    token_count: int = Field(ge=0, description="Nombre total de tokens indexés pour ce document.")


class EngineDocumentChunksPage(BaseModel):
    """Une page de fragments d'un document — retour de `list_document_chunks`.

    Regroupe les fragments, les métadonnées du document et l'état de la
    pagination dans un seul type plutôt que deux appels séparés : la route
    HTTP qui consomme ce port (Phase 13) a besoin des trois à chaque appel, et
    RAGFlow les rend déjà ensemble dans la même enveloppe.
    """

    model_config = ConfigDict(frozen=True)

    chunks: list[EngineChunk]
    document: EngineDocumentChunksMeta
    total: int = Field(ge=0, description="Nombre total de fragments, toutes pages confondues.")
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class EngineSearchResult(BaseModel):
    """Un fragment retourné par la recherche hybride du moteur.

    Les trois scores sont rendus **tels quels**, sans renormalisation : leur
    échelle dépend de l'activation du reranking (le score de reranking remplace
    la similarité vectorielle dans la combinaison). Les retoucher ici rendrait
    incomparables deux recherches menées avec des réglages différents.

    `page_start`/`page_end` localisent le fragment dans le document d'origine —
    `None` tous les deux quand le moteur ne fournit pas de position **fiable**
    (cf. `EngineSearchResult` côté adaptateur : les formats sans pagination
    renvoient un ersatz de position qu'il ne faut pas afficher). Deux champs et
    non un seul : un fragment chevauche fréquemment deux ou trois pages (fait
    vérifié), et n'annoncer que la première laisserait croire que la citation
    tient entière sur celle-ci.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_external_id: str
    content: str
    similarity: float
    vector_similarity: float
    term_similarity: float
    page_start: int | None = Field(
        default=None, ge=1, description="Première page couverte par le fragment (1-based)."
    )
    page_end: int | None = Field(
        default=None, ge=1, description="Dernière page couverte — égale à `page_start` si unique."
    )


@runtime_checkable
class IngestionEnginePort(Protocol):
    """Contrat du moteur unique d'ingestion, d'indexation et de retrieval (ADR-001).

    `runtime_checkable` sert aux assertions de test (`isinstance`) ; la
    vérification qui fait foi reste statique, via mypy, là où un adaptateur
    concret est fourni aux services.
    """

    async def ensure_dataset(
        self,
        *,
        ref: EngineDatasetRef | None,
        name_seed: str,
        embedding_provider_id: str,
        reranker_provider_id: str | None,
    ) -> EngineDatasetRef:
        """Renvoie le dataset du moteur pour une base de connaissances, en le créant au besoin.

        `ref` est la référence déjà connue de RAGEngine
        (`KnowledgeBase.ragflow_dataset_external_id`) : fournie, elle est
        réutilisée telle quelle. `name_seed` est l'**UUID** de la base, jamais
        son nom lisible — c'est lui qui rend l'opération idempotente sans
        vérification préalable d'unicité.
        """
        ...

    async def upload_document(
        self, dataset: EngineDatasetRef, *, filename: str, content: bytes
    ) -> EngineDocumentRef:
        """Téléverse un fichier dans le dataset, sans déclencher son traitement."""
        ...

    async def start_parsing(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef, profile: ChunkingProfile
    ) -> None:
        """Applique le profil de chunking puis lance le traitement du document."""
        ...

    async def get_parse_status(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef
    ) -> EngineParseStatus:
        """Projette l'état courant du document — best-effort, cf. `EngineParseStatus`."""
        ...

    async def cancel_parsing(self, dataset: EngineDatasetRef, document: EngineDocumentRef) -> None:
        """Interrompt le traitement en cours ; le document passe à `cancel` côté moteur."""
        ...

    async def search(
        self,
        datasets: Sequence[EngineDatasetRef],
        *,
        query: str,
        top_k: int,
        rerank_provider_id: str | None,
        similarity_threshold: float | None,
        document_refs: Sequence[EngineDocumentRef] | None = None,
        rerank_candidate_pool_size: int | None = None,
    ) -> list[EngineSearchResult]:
        """Recherche hybride (dense + BM25) sur un ou plusieurs datasets.

        `similarity_threshold` n'a **pas** de défaut d'adaptateur : `None` laisse
        le moteur appliquer le sien, toute autre valeur est une décision assumée
        de l'appelant, à arbitrer conjointement avec `rerank_provider_id`.

        `document_refs` restreint la recherche à un sous-ensemble de documents du
        ou des datasets interrogés (filtre nativement supporté par RAGFlow, fait
        vérifié) — `None` ne filtre rien, une séquence vide n'a rien à chercher et
        se comporte comme une absence de dataset : `[]` sans appeler le moteur.

        `rerank_candidate_pool_size` plafonne le vivier de candidats soumis au
        reranker (distinct de `top_k`, qui borne la sortie) — `None` laisse le
        moteur appliquer son propre défaut. Sans plafond, un lot de candidats
        trop grand sur des passages de longueur réelle peut dépasser le budget
        VRAM du reranker (incident confirmé le 2026-09-06, CONTRIBUTING.md §2) ;
        n'a de sens que si `rerank_provider_id` n'est pas `None`.
        """
        ...

    async def delete_document(self, dataset: EngineDatasetRef, document: EngineDocumentRef) -> None:
        """Retire un document du dataset, avec les fragments que le moteur en a indexés.

        **Pas idempotent, et c'est assumé** : un document déjà retiré fait
        répondre au moteur son `102` (« introuvable ou interdit »), traduit ici
        en `EngineError` comme partout ailleurs. Le rendre silencieux dans
        l'adaptateur masquerait du même coup un vrai défaut de droits, que
        RAGFlow rend indiscernable d'une absence. C'est à l'appelant — qui sait,
        lui, si sa propre base connaissait encore ce document — de décider ce
        qu'une erreur permanente signifie pour lui (`DocumentService.delete`).

        Raises:
            EngineError: document ou dataset inconnu du moteur, ou refusé.
            EngineTransientError: moteur momentanément indisponible — rejouable.
        """
        ...

    async def delete_dataset(self, ref: EngineDatasetRef) -> None:
        """Supprime le dataset et tout ce que le moteur en a indexé."""
        ...

    async def list_document_chunks(
        self,
        dataset: EngineDatasetRef,
        document: EngineDocumentRef,
        *,
        page: int = 1,
        page_size: int,
    ) -> EngineDocumentChunksPage:
        """Liste les fragments bruts d'un document déjà chunké, une page à la fois.

        Deux consommateurs : la génération d'évaluation (Phase 8, ADR-017),
        qui ne lit que `page=1` pour tirer un passage source — préférée à un
        `search()` scopé au document, RAGFlow exposant un vrai listing par
        document, aucune raison de simuler la même chose avec une requête dont
        le texte devrait être deviné — et la consultation des fragments depuis
        l'interface (Phase 13), qui pagine réellement un document volumineux.
        Un document sans aucun fragment (cas théoriquement impossible pour un
        document `INDEXED`, mais pas garanti par contrat) renvoie une page
        vide plutôt que d'échouer.
        """
        ...
