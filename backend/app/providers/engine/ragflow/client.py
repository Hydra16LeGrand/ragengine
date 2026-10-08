"""Client HTTP RAGFlow — implémentation d'`IngestionEnginePort`.

Tout ce que ce module encode a été **vérifié contre une instance réelle**
(RAGFlow v0.26.4), pas déduit de la documentation : endpoints, noms de champs,
casse des états, codes d'erreur. La référence normative est
`docs/ingestion-engine-architecture.md` ; ce fichier en est l'application.

Trois particularités du moteur commandent la forme du code :

1. **Les erreurs métier reviennent en HTTP 200.** Le statut réel vit dans le
   champ `code` du corps JSON (`0` = succès). Un adaptateur bâti sur
   `raise_for_status()` ne détecterait aucune erreur RAGFlow — d'où `_call`,
   par lequel passe *chaque* requête, et qui lit `code` avant toute chose.
2. **Les états de document sont en MAJUSCULES** (`UNSTART`, `RUNNING`, `CANCEL`,
   `DONE`, `FAIL`), là où le port les expose en minuscules.
3. **Il n'existe aucun endpoint de métadonnées par identifiant de document.**
   `GET /datasets/{id}/documents/{doc}` télécharge le *fichier* ; l'état se lit
   par la liste filtrée `GET /datasets/{id}/documents?id={doc}`, qui renvoie une
   liste d'un élément.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings
from app.domain.enums import JobState
from app.providers.engine.base import MAX_SEARCH_RESULTS as MAX_SEARCH_RESULTS
from app.providers.engine.base import (
    ChunkingProfile,
    EngineChunk,
    EngineChunkPosition,
    EngineDatasetRef,
    EngineDocumentChunksMeta,
    EngineDocumentChunksPage,
    EngineDocumentRef,
    EngineError,
    EngineParseStatus,
    EngineSearchResult,
    EngineTransientError,
    EngineValidationError,
)

_logger = logging.getLogger(__name__)

API_PREFIX: Final = "/api/v1"
"""Préfixe des routes REST de RAGFlow (`api/apps/__init__.py`, `url_prefix`)."""

DEFAULT_TIMEOUT_SECONDS: Final = 60.0
"""Généreux : le téléversement et le lancement d'un parsing sont synchrones."""

DATASET_NAME_PREFIX: Final = "ragengine-"
"""Préfixe des datasets créés par RAGEngine — repère humain dans l'UI RAGFlow."""

HUGGINGFACE_PROVIDER: Final = "HuggingFace"
"""Fournisseur RAGFlow natif (`rag/llm/embedding_model.py::HuggingFaceEmbed`,
`rag/llm/rerank_model.py::HuggingfaceRerank`) déclaré auprès de RAGFlow depuis
ADR-051 — remplace `Xinference` (ADR-011). Seul fournisseur de modèles
enregistré auprès de RAGFlow (`scripts/bootstrap_ragflow.py`)."""

HUGGINGFACE_EMBEDDING_INSTANCE_NAME: Final = "ragengine-inference-embedding"
HUGGINGFACE_RERANKER_INSTANCE_NAME: Final = "ragengine-inference-reranker"
"""Noms des deux instances RAGFlow enregistrées sous le fournisseur `HuggingFace`
(`scripts/bootstrap_ragflow.py`, qui importe ces constantes plutôt que de les
redéfinir — un seul endroit connaît ces chaînes). Une par conteneur TEI
mono-modèle (`docker/docker-compose.gpu.yml`) : contrairement à Xinference, qui
hébergeait BGE-M3 et bge-reranker-v2-m3 sous une même URL et donc une seule
instance, TEI impose un conteneur par modèle, donc deux `base_url` distinctes
côté RAGFlow, donc deux instances."""

_EMBEDDING_MODEL_SUFFIX: Final = f"@{HUGGINGFACE_EMBEDDING_INSTANCE_NAME}@{HUGGINGFACE_PROVIDER}"
_RERANKER_MODEL_SUFFIX: Final = f"@{HUGGINGFACE_RERANKER_INSTANCE_NAME}@{HUGGINGFACE_PROVIDER}"
"""RAGFlow identifie un modèle par `<nom>@<fournisseur>` (fait vérifié en
conditions réelles avec le fournisseur précédent, Xinference, ADR-011 :
`POST /datasets` refuse `embedding_model="bge-m3"` avec `EngineValidationError`
et n'accepte qu'un identifiant qualifié par le fournisseur ; même refus
constaté sur `rerank_id` côté `POST /retrieval`) — forme à 2 segments,
`<nom>@<fournisseur>`, résolue par `split_model_name`
(`api/db/joint_services/tenant_model_service.py`) vers l'unique instance active du
fournisseur si elle existe.

Cette forme courte ne suffit plus depuis ADR-051 : **deux** instances actives
vivent maintenant sous le même fournisseur `HuggingFace` (une par conteneur TEI),
et `split_model_name`/`_resolve_instance_for_model` lèvent alors une erreur
plutôt que de deviner laquelle utiliser (vérifié en lisant
`tenant_model_service.py` : le repli sur l'instance unique n'est appliqué que
si `len(active_instances) == 1`). Le format à **3** segments,
`<nom>@<instance>@<fournisseur>`, lève l'ambiguïté en désignant l'instance
explicitement — c'est la forme que RAGFlow supporte nativement pour ce cas
(même fonction de résolution), pas un contournement. `KnowledgeBase.
embedding_model_id` / `KnowledgeBase.reranker_model_id` restent des identifiants
nus ("bge-m3", "bge-reranker-v2-m3") côté domaine — cet adaptateur est le seul
endroit qui connaît le format attendu par RAGFlow."""

_TRANSIENT_CODES: Final[frozenset[int]] = frozenset({100, 105, 106})
"""`100` exception serveur générique, `105` erreur de connexion, `106` déjà en cours.

Les **seules** erreurs rejouables : élargir cet ensemble ferait boucler une tâche
Celery sur une faute permanente (`docs/ingestion-engine-architecture.md` §2).
"""

_VALIDATION_CODES: Final[frozenset[int]] = frozenset({101})
"""`101` = argument invalide. Permanente : rejouer donnerait le même refus."""

_UPLOAD_INTEGRITY_MAX_ATTEMPTS: Final = 4
"""Tentatives de téléversement avant d'abandonner (corruption ou vérification
impossible), ADR-044 §5.1 et sa correction du 2026-09-13.

RAGFlow sert son API par le serveur de développement Werkzeug (`app.run()`),
qui corrompt parfois le corps d'un envoi multipart sous requêtes concurrentes —
mesuré à ~37,5 % d'échecs par tentative sur le cas capturé (`.xlsx`), même
mécanisme observé sur un `.jpg`. Le taux composé après 4 tentatives
indépendantes tombe à ≈ 2 % (0,375⁴) : pas nul, mais assez rare pour rester
l'exception que `POST /jobs/{id}/retry` couvre, plutôt que la règle qui ferait
boucler indéfiniment une tâche Celery sur une panne qui ne se résorbe jamais.
Même ordre de grandeur que `test_office.py` (suite de mesure interne)
_ENGINE_TRANSIENT_INGEST_RETRIES`, qui rejoue le même défaut côté harnais de
mesure — pas une coïncidence, la même panne mesurée au même endroit."""

_UPLOAD_RETRY_BACKOFF_SECONDS: Final[tuple[float, ...]] = (1.0, 2.0, 4.0)
"""Attente croissante entre deux tentatives de téléversement — la dernière
tentative n'attend jamais rien, elle échoue ou réussit directement."""

_RUN_STATES: Final[Mapping[str, str]] = {
    "UNSTART": "unstart",
    "RUNNING": "running",
    "CANCEL": "cancel",
    "DONE": "done",
    "FAIL": "fail",
}
"""Casse réelle renvoyée par RAGFlow → casse du port.

`SCHEDULE` (valeur interne `"5"`) existe dans le code du moteur mais n'a jamais
été observé et n'a pas de correspondance ici : le rencontrer lève une
`EngineError` explicite plutôt qu'une projection inventée
(`docs/ingestion-engine-architecture.md` §7).
"""

INFERENCE_UNREACHABLE_MESSAGE: Final = (
    "Le service d'inférence (embeddings et reranking) est injoignable : le moteur "
    "d'ingestion n'a pas pu vectoriser le contenu. Démarrez la pile GPU "
    "(`docker/docker-compose.gpu.yml`, services `inference-embedding` et "
    "`inference-reranker`), puis relancez l'opération."
)
"""Message rendu à la place de la trace brute du moteur quand l'inférence est tombée.

Cause vérifiée en conditions réelles sous Xinference (conteneur arrêté, ingestion
et recherche rejouées) : RAGFlow ne distingue pas cette panne d'une autre. Elle
ressort verbatim, et sans le moindre indice actionnable, sous deux formes —
`EmbeddingError('Embedding request failed for XinferenceEmbed. Error: Connection
error.')` dans le corps d'une réponse de recherche (`code=100`), et
`[ERROR][Exception]: Embedding request failed for XinferenceEmbed. Error:
Connection error.` dans le journal de progression d'un document `FAIL`. Les deux
atterrissaient tels quels sous les yeux de l'utilisateur. Depuis ADR-051
(migration vers TEI), le nom de classe relayé par RAGFlow devient
`HuggingFaceEmbed` (`rag/llm/embedding_model.py`, `_FACTORY_NAME = "HuggingFace"`) :
`_INFERENCE_PROVIDER_MARKER` a été ajusté en conséquence, le mécanisme de
détection reste identique.
"""

_INFERENCE_PROVIDER_MARKER: Final = "HuggingFace"
"""`HuggingFace` est le **seul** fournisseur de modèles déclaré auprès de RAGFlow
(ADR-051, `scripts/bootstrap_ragflow.py`) : le voir nommé dans un échec de
connexion du moteur désigne donc l'un de nos conteneurs TEI, sans ambiguïté.
Repris de `Xinference` (ADR-011) — même mécanisme, fournisseur renommé."""

_CONNECTION_FAILURE_MARKERS: Final[tuple[str, ...]] = (
    "Connection error",
    "Connection refused",
    "ConnectError",
    "Max retries exceeded",
)
"""Seul `Connection error` a été observé verbatim ; les autres sont les formes
voisines du même mode d'échec (socket refusée côté `httpx`/`requests`). Ne pas
les reconnaître ne coûterait qu'un retour au message générique — les inclure
n'ajoute donc aucun risque de masquer une autre panne."""

_INDEXING_MARKER: Final = "Indexing done"
_EMBEDDING_MARKER: Final = "Embedding chunks"
_CHUNKING_MARKERS: Final[tuple[str, ...]] = (
    "Task is queued",
    "Task has been received",
    "Start to parse",
)
"""Marqueurs de `progress_msg` observés, **communs à tous les `chunk_method`**.

Les lignes propres au parseur (`OCR started`, `Extract records`…) sont
délibérément absentes : elles varient d'un template à l'autre et n'ont rien de
contractuel. Ces trois-là ont été vues sur un PDF texte, un PDF scanné et un
classeur — c'est ce qui autorise à les traiter comme le tronc commun.
"""


class _RagflowDocument(BaseModel):
    """Vue d'un document RAGFlow réduite aux champs dont dépend la projection d'état."""

    model_config = ConfigDict(extra="ignore")

    id: str
    run: str = "UNSTART"
    progress: float = 0.0
    progress_msg: str = ""
    chunk_count: int = 0
    size: int | None = None
    """Octets stockés côté moteur pour ce document. Seul champ de ce modèle qui
    n'a **pas** été confirmé test par test contre l'instance déployée par cet
    adaptateur (contrairement au reste de cette classe, cf. tête de fichier) —
    sa provenance est l'audit d'ADR-044 §5.1, qui a lu `size=5050` en
    interrogeant le moteur sur un document dont l'upload avait corrompu 2
    octets. `None` s'il est absent de la réponse : `_uploaded_size` traite
    alors la vérification d'intégrité comme indisponible plutôt que comme un
    échec — une version de RAGFlow qui cesserait de le rendre désactiverait
    silencieusement ce garde-fou plutôt que de faire échouer tout upload."""


class _RagflowChunk(BaseModel):
    """Un fragment de la réponse de recherche, après le renommage opéré par RAGFlow."""

    model_config = ConfigDict(extra="ignore")

    id: str
    document_id: str
    content: str = ""
    similarity: float = 0.0
    vector_similarity: float = 0.0
    term_similarity: float = 0.0
    positions: list[list[float]] = Field(default_factory=list)
    """Rectangles d'origine du fragment, `[page, x0, x1, haut, bas]` — cf. `_page_range_of`.

    Typé en `float` et non en `int` : RAGFlow rend ces coordonnées telles que
    son analyse de mise en page les a calculées, sans garantie qu'elles soient
    entières. Un modèle plus strict ferait échouer *toute* la réponse de
    recherche sur une décimale, alors que la position n'est qu'un agrément
    d'affichage.
    """


def _page_range_of(positions: Sequence[Sequence[float]]) -> tuple[int, int] | None:
    """Déduit l'intervalle de pages couvert par un fragment, ou `None` si non fiable.

    Forme réelle de `positions` sur `POST /retrieval` (vérifiée contre
    l'instance déployée, RAGFlow v0.26.4) : une liste de quintuplets
    `[page, x0, x1, haut, bas]`, un par rectangle de texte retenu, où `page` est
    **1-based** — confirmé en recoupant le texte des fragments avec celui des
    pages réelles de deux PDF distincts (correspondance exacte sur tous les
    fragments tenant sur une seule page). Un fragment chevauche couramment deux
    ou trois pages, d'où un intervalle plutôt qu'une page unique.

    **Le filtre sur la boîte englobante n'est pas de la prudence décorative.**
    Les formats sans pagination renvoient eux aussi un `positions` non vide,
    mais dégénéré : le document Word audité rend `[[1, 0, 0, 0, 0]]`,
    `[[2, 1, 1, 1, 1]]`, … — c'est le **rang du fragment**, pas une page, avec
    une boîte d'aire nulle. L'afficher tel quel annoncerait « page 5 » pour le
    cinquième paragraphe d'un document qui n'a pas de pages. Un rectangle réel
    vérifie toujours `x0 < x1` et `haut < bas` (3290 rectangles de PDF audités,
    aucune exception ; 12 rectangles Word, tous dégénérés) : c'est ce qui
    sépare une position exploitable d'un numéro d'ordre déguisé.
    """
    pages = [
        int(box[0])
        for box in positions
        if len(box) == 5 and box[1] < box[2] and box[3] < box[4] and box[0] >= 1
    ]
    if not pages:
        return None
    return min(pages), max(pages)


class _RagflowSearchData(BaseModel):
    """Enveloppe de `POST /retrieval`."""

    model_config = ConfigDict(extra="ignore")

    chunks: list[_RagflowChunk] = Field(default_factory=list)


class _RagflowRawChunk(BaseModel):
    """Un élément de `GET /datasets/{id}/documents/{doc}/chunks`.

    Forme réelle observée (Phase 8, RAGFlow v0.26.4) : `id`, `content`,
    `document_id`, `dataset_id`, `docnm_kwd`, `positions`, `image_id`,
    `important_keywords`, `questions`, `available`, `tag_kwd`. Seuls `id` et
    `content` intéressaient la génération d'évaluation (Phase 8) ; la
    consultation des fragments depuis l'interface (Phase 13) consomme aussi
    `positions`, `important_keywords`, `questions` et `available` — `docnm_kwd`,
    `image_id`, `tag_kwd`, `document_id`, `dataset_id` restent sans usage
    identifié, toujours ignorés.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    content: str = ""
    positions: list[list[float]] = Field(default_factory=list)
    important_keywords: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    available: bool = True


class _RagflowChunkListDoc(BaseModel):
    """L'objet `doc` de l'enveloppe : métadonnées du document, pas d'un fragment."""

    model_config = ConfigDict(extra="ignore")

    chunk_method: str | None = None
    chunk_count: int = 0
    token_count: int = 0


class _RagflowChunkListData(BaseModel):
    """Enveloppe de `GET /datasets/{id}/documents/{doc}/chunks`."""

    model_config = ConfigDict(extra="ignore")

    chunks: list[_RagflowRawChunk] = Field(default_factory=list)
    doc: _RagflowChunkListDoc = Field(default_factory=_RagflowChunkListDoc)
    total: int = 0


def _document_order_key(positions: Sequence[Sequence[float]]) -> tuple[int, float] | None:
    """Clé de tri `(page, haut)` reflétant la position du fragment dans le document.

    `None` quand aucun rectangle fiable n'est disponible — même filtre que
    `_page_range_of` (rectangle non dégénéré : `x0 < x1`, `haut < bas`,
    `page >= 1`), pour la même raison : un rectangle dégénéré n'est pas une
    position, c'est un rang de fragment déguisé.

    **`GET /datasets/{id}/documents/{doc}/chunks` ne rend pas l'ordre du
    document**, contrairement à ce qu'annonce le moteur (Phase 13, `TEST
    PIPELINE`, document `competition_nationale_startups_cgu.pdf` : ordre réel
    des phrases `2 → 3 → 0 → 1 → 4`, ordre rendu `0,1,2,3,4`). Cause établie en
    lisant le code source déployé (`rag/nlp/search.py`,
    `list_chunks`/`chunk_api.py`) : l'endpoint trie bien par
    `chunk_order_int, page_num_int, top_int, create_timestamp_flt` (`sort:
    True`), mais **sous `layout_recognize: "Plain Text"`, aucun des trois
    premiers champs n'est jamais renseigné** (l'analyse de mise en page est
    sautée, fait établi 8 de la Phase 11) — confirmé en lisant directement
    l'index Elasticsearch du moteur (`_source` sans `chunk_order_int` ni
    `page_num_int` ni `top_int` sur les 5 fragments du document cité). Le tri
    retombe alors sur `create_timestamp_flt`, stocké en `float` ES (32 bits) :
    les fragments d'un même document sont créés à quelques centaines de
    microsecondes d'écart, un écart plus fin que la précision de ce type — les
    5 valeurs de l'exemple ci-dessus arrondissent **au même flottant** une
    fois passées par le tri ES (vérifié en rejouant le tri exact du moteur
    contre l'index réel). Le tri se réduit donc à une égalité totale, dont
    l'issue est l'ordre interne de Lucene/ES (segment d'indexation), sans
    rapport avec l'ordre du document.

    Sous `DeepDOC`, à l'inverse, `page_num_int`/`top_int` sont renseignés (fait
    vérifié : document `delimiter-period__27-pdf-long-50pages.pdf` du corpus
    de test, 10 fragments, pages croissantes 1, 6, 12, 13… dans l'ordre rendu)
    — c'est cette information, disponible dans `positions`, que cette fonction
    exploite pour retrier côté RAGEngine plutôt que de faire confiance au tri
    déjà appliqué par le moteur.

    Aucun champ de repli fiable n'existe pour `Plain Text` : ni
    `chunk_order_int` (absent du mapping de cette version du moteur), ni
    `create_timestamp_flt` (fait ci-dessus). `list_document_chunks` documente
    donc cette limite plutôt que d'inventer un ordre — cf. son docstring.
    """
    valid = [
        box
        for box in positions
        if len(box) == 5 and box[1] < box[2] and box[3] < box[4] and box[0] >= 1
    ]
    if not valid:
        return None
    first = min(valid, key=lambda box: (box[0], box[3]))
    return (int(first[0]), first[3])


def _chunk_positions_of(positions: Sequence[Sequence[float]]) -> list[EngineChunkPosition]:
    """Convertit les quintuplets bruts `[page, x0, x1, haut, bas]` en `EngineChunkPosition`.

    Un rectangle malformé (pas cinq éléments) est simplement écarté plutôt que
    de faire échouer tout le listing — même posture défensive que `_page_range_of`,
    qui filtre les rectangles dégénérés en aval plutôt qu'ici : cette fonction
    ne fait que la conversion de forme, pas le jugement sur leur fiabilité
    (`EngineChunk.page_range` s'en charge).
    """
    return [
        EngineChunkPosition(page=box[0], x0=box[1], x1=box[2], top=box[3], bottom=box[4])
        for box in positions
        if len(box) == 5
    ]


_REPEATED_SPACE_PATTERN: Final = re.compile(r"[ \t]{2,}")
"""2+ espaces/tabulations consécutifs — jamais `\\n`, `re.compile` sans `DOTALL`
ni classe `\\s` ne matche pas les sauts de ligne."""

_TABLE_MARKER_PATTERN: Final = re.compile(r"<table", re.IGNORECASE)
"""Détection volontairement large : la présence de `<table` suffit à désactiver
toute la normalisation du fragment, cf. `normalize_chunk_whitespace`."""


def normalize_chunk_whitespace(content: str) -> str:
    """Fusionne les espaces/tabulations consécutifs en un seul espace.

    Correctif du diagnostic de la Phase 13 (`scripts/
    measure_mid_sentence_and_spacing.py`) : sous `layout_recognize: "Plain
    Text"`, `pypdf.extract_text()` — utilisé en interne par RAGFlow — insère des
    doubles espaces entre les mots sur du texte justifié (13,7 % à 97,6 % des
    espacements inter-mots mesurés sur deux documents réels). RAGFlow ne
    normalise pas ce texte lui-même ; ce post-traitement reste conforme à
    l'ADR-001 (RAGFlow reste l'unique moteur d'extraction, on ne touche qu'au
    texte qu'il a déjà produit).

    Deux exclusions délibérées :
    - **Les sauts de ligne ne sont jamais touchés** : la mise en forme
      verticale (paragraphes, listes) doit rester intacte. Le motif ne
      capture que `[ \\t]`, jamais `\\n`.
    - **Le contenu portant `<table` (insensible à la casse) est laissé tel
      quel, en entier** : l'espacement dans les cellules d'un tableau HTML
      peut porter du sens visuel/alignement (cas délibérément exclu du
      diagnostic précédent).
    """
    if _TABLE_MARKER_PATTERN.search(content):
        return content
    return _REPEATED_SPACE_PATTERN.sub(" ", content)


def dataset_name_for(name_seed: str) -> str:
    """Construit le nom RAGFlow d'un dataset à partir de l'UUID d'une base de connaissances.

    Jamais à partir du nom lisible : RAGFlow n'oppose pas d'erreur à un nom
    dupliqué, il renomme en silence (`audit-kb` → `audit-kb-renamed(1)`, fait
    observé). Dériver du seul UUID rend la création idempotente sans vérifier
    l'unicité au préalable, et supprime une classe entière de doublons
    silencieux après une reprise sur panne.
    """
    return f"{DATASET_NAME_PREFIX}{name_seed}"


def infer_phase(run: str, progress_msg: str) -> JobState | None:
    """Projette `chunking → embedding → indexing` depuis le journal de progression (§3).

    Heuristique documentée, **pas un contrat** : RAGFlow n'expose aucune phase
    fine. `run == "done"` tranche sans ambiguïté ; sinon on cherche des marqueurs
    textuels, du plus avancé au moins avancé. Aucun marqueur reconnu ⇒ `None` —
    y compris sur le journal vide du bug de blocage (`run=RUNNING`,
    `progress=0.0`, `progress_msg=""`). Ne jamais deviner : le service de veille
    se contente alors de journaliser `last_message`.
    """
    if run == "done":
        return JobState.COMPLETED
    if _INDEXING_MARKER in progress_msg:
        return JobState.INDEXING
    if _EMBEDDING_MARKER in progress_msg:
        return JobState.EMBEDDING
    if any(marker in progress_msg for marker in _CHUNKING_MARKERS):
        return JobState.CHUNKING
    return None


def is_inference_unreachable(message: str) -> bool:
    """Reconnaît, dans un message du moteur, la panne du service d'inférence GPU.

    Le moteur ne code pas cette cause : elle n'a pas de `code` à elle (l'échec de
    recherche revient en `code=100`, celui d'ingestion n'a même pas de code du
    tout, il arrive par le journal de progression). Le seul signal disponible est
    donc textuel — le nom du fournisseur *et* un échec de connexion, les deux
    exigés ensemble pour qu'une erreur de modèle sans rapport ne soit pas
    requalifiée à tort.
    """
    return _INFERENCE_PROVIDER_MARKER in message and any(
        marker in message for marker in _CONNECTION_FAILURE_MARKERS
    )


def actionable_engine_message(message: str) -> str:
    """Substitue un message exploitable à la trace brute du moteur, quand la cause est connue.

    Un seul cas aujourd'hui : le service d'inférence arrêté. La trace d'origine
    est conservée en queue — l'utilisateur a besoin de savoir quoi faire, mais
    quiconque diagnostique a besoin de ce que le moteur a réellement dit.

    Un second cas existait sous Xinference (ADR-011) : le reranker qui répond
    mais dont le modèle demandé n'est pas encore chargé, le temps d'un
    rechargement par la boucle de réconciliation `ragengine-inference-models`
    (`is_rerank_model_loading`, retiré par ADR-051). **Sans équivalent sous
    TEI**, par construction et pas seulement en pratique : un conteneur TEI n'a
    ni registre de modèles ni boucle de réconciliation — il charge son unique
    modèle une fois au démarrage (`--model-id`), et le healthcheck
    (`docker-compose.gpu.yml`) ne le déclare sain qu'une fois ce chargement
    terminé. Un conteneur TEI joignable après son healthcheck sert donc
    toujours son modèle ; la distinction « le service répond mais pas le bon
    modèle » n'a plus de scénario qui la déclenche.
    """
    if is_inference_unreachable(message):
        return f"{INFERENCE_UNREACHABLE_MESSAGE} (détail du moteur : {message})"
    return message


def last_progress_line(progress_msg: str) -> str | None:
    """Dernière ligne non vide du journal, ou `None` s'il n'y en a aucune.

    `progress_msg` s'ouvre sur un saut de ligne et accumule des lignes horodatées
    (forme observée : `"\\n20:53:58 Task has been received.\\n…"`).
    """
    lines = [line.strip() for line in progress_msg.splitlines() if line.strip()]
    return lines[-1] if lines else None


class RagflowEngineAdapter:
    """Adaptateur HTTP RAGFlow conforme à `IngestionEnginePort`.

    Construire l'adaptateur n'ouvre aucune socket : le client `httpx` est créé à
    la première requête (`docs/providers-architecture.md` §8), pour qu'inspecter
    la configuration d'une base ne coûte jamais une connexion.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, settings: Settings) -> RagflowEngineAdapter:
        """Construit l'adaptateur depuis la configuration (aucun secret en dur).

        Une clé absente est une erreur de déploiement, pas d'exécution : sans
        elle, RAGFlow répondrait `code=102` sur chaque appel, message compris,
        ce qui masquerait la vraie cause. `scripts/bootstrap_ragflow.py` est la
        procédure qui l'émet.
        """
        if settings.ragflow_api_key is None:
            message = (
                "RAGFLOW_API_KEY n'est pas renseignée : exécute "
                "`scripts/bootstrap_ragflow.py` puis reporte la clé dans `.env`."
            )
            raise EngineError(message)
        return cls(
            base_url=settings.ragflow_base_url,
            api_key=settings.ragflow_api_key.get_secret_value(),
        )

    # ─── Opérations du port ──────────────────────────────────────────────────

    async def ensure_dataset(
        self,
        *,
        ref: EngineDatasetRef | None,
        name_seed: str,
        embedding_provider_id: str,
        reranker_provider_id: str | None,
    ) -> EngineDatasetRef:
        """Réutilise la référence connue, ou crée le dataset nommé depuis l'UUID.

        `reranker_provider_id` est **sans effet ici** : le dataset RAGFlow ne
        porte pas de reranker (vérifié sur son modèle de données, qui n'a que
        `embd_id`). Le reranking est un paramètre de recherche, transmis par
        `search(rerank_provider_id=...)`. Le port garde l'argument parce qu'un
        autre moteur pourrait le lier au dataset ; RAGFlow, lui, l'ignore, et
        c'est dit plutôt que masqué.
        """
        if ref is not None:
            return ref
        payload: dict[str, JsonValue] = {
            "name": dataset_name_for(name_seed),
            "embedding_model": f"{embedding_provider_id}{_EMBEDDING_MODEL_SUFFIX}",
        }
        data = await self._call("POST", "/datasets", json_body=payload)
        return EngineDatasetRef(external_id=self._require_str(data, "id", "création du dataset"))

    async def upload_document(
        self, dataset: EngineDatasetRef, *, filename: str, content: bytes
    ) -> EngineDocumentRef:
        """Téléverse un fichier sans lancer son traitement (`run` reste `UNSTART`).

        **Rejeu interne sur corruption confirmée** (ADR-044 §5.1, correction du
        2026-09-13). RAGFlow corrompt parfois le corps d'un envoi multipart sous
        requêtes concurrentes — 2 octets `\\r\\n` préfixés au contenu réel,
        mesuré sur un `.xlsx` et un `.jpg`. Le téléversement **répond pourtant
        succès** (`code=0`, un identifiant de document valide) : `_call` ne voit
        rien, la corruption ne se révèle que dans le contenu stocké. Elle est
        donc vérifiée ici, juste après l'upload, par relecture de la taille
        enregistrée côté moteur (`_uploaded_size`) — jamais supposée absente
        faute d'exception.

        **Idempotence.** RAGFlow ne déduplique jamais un document par lui-même
        (même défaut que le nom de dataset, cf. `dataset_name_for`) : chaque
        `POST` réussi, corrompu ou non, crée une entrée distincte. Rejouer sans
        nettoyer laisserait donc un document fantôme par tentative. Le document
        dont l'intégrité n'a pas pu être confirmée est donc supprimé
        (`_delete_corrupted_upload`, best-effort) avant tout nouvel essai.

        Bornée à `_UPLOAD_INTEGRITY_MAX_ATTEMPTS` tentatives, attente croissante
        entre deux (`_UPLOAD_RETRY_BACKOFF_SECONDS`) : au-delà, l'échec remonte
        en `EngineTransientError`, comme toute panne moteur momentanée — jamais
        une boucle sans fin, c'est `POST /jobs/{id}/retry` qui reprend un job
        réellement en échec.
        """
        attempt = 1
        while True:
            data = await self._call(
                "POST",
                f"/datasets/{dataset.external_id}/documents",
                files={"file": (filename, content)},
            )
            if not isinstance(data, list) or not data:
                raise EngineError("Le moteur n'a renvoyé aucun document après le téléversement.")
            document_ref = EngineDocumentRef(
                external_id=self._require_str(data[0], "id", "téléversement du document")
            )
            reason = await self._integrity_failure(
                dataset, document_ref, expected_size=len(content)
            )
            if reason is None:
                return document_ref
            _logger.warning(
                "Téléversement à retenter",
                extra={
                    # Jamais `filename` : clé réservée par `logging.LogRecord`
                    # (le nom du fichier source de l'appel de log), qui fait
                    # échouer `makeRecord` sur un `KeyError` si on l'écrase.
                    "document_filename": filename,
                    "document_id": document_ref.external_id,
                    "reason": reason,
                    "attempt": attempt,
                    "max_attempts": _UPLOAD_INTEGRITY_MAX_ATTEMPTS,
                },
            )
            await self._delete_corrupted_upload(dataset, document_ref)
            if attempt >= _UPLOAD_INTEGRITY_MAX_ATTEMPTS:
                message = (
                    "Le moteur d'ingestion a renvoyé un contenu corrompu ou invérifiable "
                    f"après {_UPLOAD_INTEGRITY_MAX_ATTEMPTS} tentatives de téléversement "
                    f"de « {filename} » ({reason})."
                )
                raise EngineTransientError(message)
            await asyncio.sleep(_UPLOAD_RETRY_BACKOFF_SECONDS[attempt - 1])
            attempt += 1

    async def _integrity_failure(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef, *, expected_size: int
    ) -> str | None:
        """Diagnostique un envoi tout juste réussi. `None` si rien ne le contredit.

        Rend une raison lisible plutôt qu'un booléen : elle finit dans le
        journal de rejeu et, si les tentatives s'épuisent, dans le message de
        `EngineTransientError` vu par l'appelant.

        Une panne transitoire *pendant la vérification elle-même* (moteur
        momentanément injoignable sur la relecture) est traitée comme un motif
        de rejeu au même titre qu'une taille incohérente : on ne sait alors pas
        si l'envoi qui vient de réussir est intact, et le supposer serait
        exactement l'erreur que cette vérification existe pour éviter.
        """
        try:
            uploaded_size = await self._uploaded_size(dataset, document)
        except EngineTransientError as error:
            return f"vérification d'intégrité impossible ({error})"
        if uploaded_size is None:
            return None
        if uploaded_size == expected_size:
            return None
        return f"taille stockée {uploaded_size} ≠ taille envoyée {expected_size}"

    async def _uploaded_size(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef
    ) -> int | None:
        """Relit la taille enregistrée côté moteur pour un document tout juste téléversé.

        Même endpoint que `get_parse_status` — aucun endpoint de métadonnées
        par identifiant n'existe (cf. docstring de tête, point 3) :
        `GET /datasets/{id}/documents?id=...`.
        """
        data = await self._call(
            "GET",
            f"/datasets/{dataset.external_id}/documents",
            params={"id": document.external_id, "page_size": "1"},
        )
        raw = self._first_document(data, document)
        return raw.size

    async def _delete_corrupted_upload(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef
    ) -> None:
        """Nettoie un document dont l'intégrité n'a pas pu être confirmée, avant nouvel essai.

        Best-effort : si la suppression échoue à son tour (moteur momentanément
        injoignable), on retente quand même le téléversement plutôt que de
        bloquer dessus. Un document orphelin jamais soumis au découpage n'est
        pas indexé et ne fausse donc aucune recherche — coût de stockage
        mineur, pas un défaut fonctionnel.
        """
        try:
            await self.delete_document(dataset, document)
        except EngineError:
            _logger.warning(
                "Nettoyage du document non confirmé impossible, nouvelle tentative quand même",
                extra={"document_id": document.external_id, "dataset_id": dataset.external_id},
            )

    async def start_parsing(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef, profile: ChunkingProfile
    ) -> None:
        """Applique le profil de chunking, puis déclenche le traitement.

        Deux appels, dans cet ordre : RAGFlow n'accepte pas de profil dans la
        requête de lancement, le template et `parser_config` se posent sur le
        document. La mise à jour est faite avant, sinon le moteur démarrerait
        avec le template hérité du dataset (`naive`).

        Le lancement passe par `POST /datasets/{id}/chunks`, l'endpoint public
        exercé en audit. `POST /datasets/{id}/documents/parse` existe aussi mais
        n'a jamais été appelé (`docs/ingestion-engine-architecture.md` §7) : on
        n'y bascule pas sans l'avoir vérifié.

        **Un profil vide ne déclenche aucune mise à jour**, et ce n'est pas une
        micro-optimisation : sur un document visuel (image), RAGFlow rejette la
        requête de mise à jour dès qu'elle porte un `chunk_method`, quelle qu'en
        soit la valeur — `Not supported yet!`, le document échoue avant d'avoir
        produit le moindre fragment. Il a de toute façon déjà posé `picture` à
        l'upload. `chunk_method = None` (`ChunkingProfile`) est la façon dont
        l'appelant dit « n'impose rien » ; sans `parser_config` non plus, il n'y
        a plus rien à mettre à jour, et l'appel est sauté.
        """
        profile_payload: dict[str, JsonValue] = {}
        if profile.chunk_method is not None:
            profile_payload["chunk_method"] = profile.chunk_method
        if profile.parser_config:
            profile_payload["parser_config"] = dict(profile.parser_config)
        if profile_payload:
            await self._call(
                "PATCH",
                f"/datasets/{dataset.external_id}/documents/{document.external_id}",
                json_body=profile_payload,
            )
        await self._call(
            "POST",
            f"/datasets/{dataset.external_id}/chunks",
            json_body={"document_ids": [document.external_id]},
        )

    async def get_parse_status(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef
    ) -> EngineParseStatus:
        """Lit l'état du document et le projette (§3), sans jamais inventer de phase."""
        data = await self._call(
            "GET",
            f"/datasets/{dataset.external_id}/documents",
            params={"id": document.external_id, "page_size": "1"},
        )
        raw = self._first_document(data, document)
        run = _RUN_STATES.get(raw.run.upper())
        if run is None:
            raise EngineError(f"État de document inconnu renvoyé par le moteur : « {raw.run} ».")
        return EngineParseStatus(
            run=run,  # type: ignore[arg-type]  # `_RUN_STATES` ne produit que le Literal du port.
            inferred_phase=infer_phase(run, raw.progress_msg),
            # Le contrat du port borne `progress` à [-1, 1] : une valeur hors
            # bornes d'une future version ne doit pas casser tout le polling.
            progress=max(-1.0, min(1.0, raw.progress)),
            last_message=last_progress_line(raw.progress_msg),
            chunk_count=max(0, raw.chunk_count),
            # `error` part tel quel dans `IngestionJob.error.message`, donc sous
            # les yeux de l'utilisateur : c'est le dernier point où une cause
            # connue peut encore être traduite en consigne exploitable.
            error=self._failure_detail(raw.progress_msg) if run == "fail" else None,
        )

    async def cancel_parsing(self, dataset: EngineDatasetRef, document: EngineDocumentRef) -> None:
        """Interrompt le traitement — le document passe proprement à `CANCEL` (vérifié).

        C'est le geste dont dépend la veille anti-blocage (ADR-012, décision 2) :
        un document peut rester `RUNNING` indéfiniment sur un bug du moteur, et
        rien côté RAGFlow ne l'en sort.
        """
        await self._call(
            "POST",
            f"/datasets/{dataset.external_id}/documents/stop",
            json_body={"document_ids": [document.external_id]},
        )

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
        """Recherche hybride, en laissant l'appelant maître du seuil de similarité.

        `top_k` désigne ici le **nombre de fragments rendus**, donc `page_size`
        côté RAGFlow — et non son paramètre homonyme `top_k`, qui dimensionne le
        vivier de candidats de la recherche vectorielle (défaut 1024) sans
        limiter la sortie. Confondre les deux plafonnerait silencieusement les
        résultats à 30, la valeur par défaut de `page_size`.

        `rerank_candidate_pool_size`, quand fourni, alimente précisément ce
        `top_k` RAGFlow — c'est le seul point qui le fait. Incident réel du
        2026-09-06 : sans plafond, RAGFlow soumettait jusqu'à 1024 candidats en
        un seul lot au reranker externe ; sur des passages de longueur réelle,
        Xinference a renvoyé `CUDA out of memory` en tentant d'allouer 6,43 Gio
        pour ce lot (`code=100`, traduit en `EngineTransientError` — le message
        « modèle en cours de rechargement » qui en résultait était donc trompeur :
        la vraie cause n'était pas un rechargement, mais un lot trop grand,
        déterministe dès qu'un vrai document était reranké). Sans effet quand
        `rerank_provider_id` est `None` : RAGFlow n'appelle alors aucun reranker,
        il n'y a rien à plafonner.

        `similarity_threshold=None` n'envoie rien : le moteur applique alors le
        sien. Aucune valeur n'est substituée ici — activer un reranker change
        l'échelle du score combiné, et un défaut d'adaptateur viderait les
        résultats sans erreur (fait vérifié).

        `rerank_provider_id` est reçu **nu** (ex. `"bge-reranker-v2-m3"`, tel que
        porté par `KnowledgeBase.reranker_model_id`) : c'est cet adaptateur, seul
        point qui connaît le format RAGFlow, qui lui applique
        `_RERANKER_MODEL_SUFFIX` — comme `ensure_dataset` le fait déjà pour
        `embedding_model` (fait vérifié sous Xinference, ADR-011 : `rerank_id` nu
        est refusé par `POST /retrieval` avec `LookupError('Provider  not found
        for model ...')`).

        `document_refs` restreint la recherche aux documents listés (filtre natif
        RAGFlow, fait vérifié) ; une séquence vide se comporte comme l'absence de
        dataset, par symétrie défensive avec `datasets`.
        """
        if not datasets:
            return []
        if document_refs is not None and not document_refs:
            return []
        if top_k < 1 or top_k > MAX_SEARCH_RESULTS:
            message = f"`top_k` doit être compris entre 1 et {MAX_SEARCH_RESULTS} (reçu {top_k})."
            raise EngineValidationError(message)
        payload: dict[str, JsonValue] = {
            "dataset_ids": [dataset.external_id for dataset in datasets],
            "question": query,
            "page": 1,
            "page_size": top_k,
        }
        if rerank_provider_id is not None:
            payload["rerank_id"] = f"{rerank_provider_id}{_RERANKER_MODEL_SUFFIX}"
            if rerank_candidate_pool_size is not None:
                payload["top_k"] = rerank_candidate_pool_size
        if similarity_threshold is not None:
            payload["similarity_threshold"] = similarity_threshold
        if document_refs:
            payload["document_ids"] = [ref.external_id for ref in document_refs]
        data = await self._call("POST", "/retrieval", json_body=payload)
        parsed = self._parse(_RagflowSearchData, data, "réponse de recherche")
        results: list[EngineSearchResult] = []
        for chunk in parsed.chunks:
            page_range = _page_range_of(chunk.positions)
            results.append(
                EngineSearchResult(
                    chunk_id=chunk.id,
                    document_external_id=chunk.document_id,
                    content=normalize_chunk_whitespace(chunk.content),
                    similarity=chunk.similarity,
                    vector_similarity=chunk.vector_similarity,
                    term_similarity=chunk.term_similarity,
                    page_start=None if page_range is None else page_range[0],
                    page_end=None if page_range is None else page_range[1],
                )
            )
        return results

    async def delete_document(self, dataset: EngineDatasetRef, document: EngineDocumentRef) -> None:
        """Retire un document du dataset — `DELETE /datasets/{id}/documents`.

        Endpoint et forme du corps **vérifiés contre l'instance déployée**
        (RAGFlow v0.26.4), pas repris de la documentation : la suppression se
        demande par lot sur la collection (`{"ids": [...]}`), au singulier près
        du `/datasets` global de `delete_dataset`, et répond
        `{"code": 0, "data": {"deleted": 1}}`. Le listing du dataset tombe bien
        à `total: 0` juste après.

        Rejouer la même suppression, ou viser un identifiant inconnu, répond
        `code=102` (« These documents do not belong to dataset … or Document not
        found: … ») — donc une `EngineError` générique, exactement comme un
        dataset qui n'appartiendrait pas au tenant. Ces deux cas sont
        indiscernables ici, et le port dit pourquoi ils ne sont pas avalés à ce
        niveau.
        """
        await self._call(
            "DELETE",
            f"/datasets/{dataset.external_id}/documents",
            json_body={"ids": [document.external_id]},
        )

    async def delete_dataset(self, ref: EngineDatasetRef) -> None:
        """Supprime le dataset et tout ce que le moteur en avait indexé."""
        await self._call("DELETE", "/datasets", json_body={"ids": [ref.external_id]})

    async def list_document_chunks(
        self,
        dataset: EngineDatasetRef,
        document: EngineDocumentRef,
        *,
        page: int = 1,
        page_size: int,
    ) -> EngineDocumentChunksPage:
        """Liste les fragments d'un document, une page à la fois.

        Endpoint vérifié en conditions réelles contre l'instance déployée
        (absent de l'audit initial de la Phase 4, `docs/ingestion-engine-
        architecture.md`) : `GET /datasets/{id}/documents/{doc}/chunks` répond
        `{"code": 0, "data": {"chunks": [...], "doc": {...}, "total": n}}` —
        même enveloppe `code`/`data` que le reste de l'API, donc traduite par le
        même `_call`. `page`/`page_size` sont transmis tels quels : RAGFlow les
        accepte nativement (§8, `docs/ingestion-engine-architecture.md`), la
        Phase 8 se contentait de la première page, la Phase 13 pagine
        réellement un document volumineux. Un document inconnu ou hors tenant
        répond `code=102` (« You don't own the document … »), traduit en
        `EngineError` générique comme partout ailleurs dans cet adaptateur (§0).

        **La page est retriée ici par `(page, haut)`** avant d'être rendue —
        le tri annoncé par le moteur (`sort: True`) ne reflète pas toujours
        l'ordre du document, cf. `_document_order_key`. Un fragment sans
        rectangle fiable (`positions` vide ou dégénéré — systématique sous
        `layout_recognize: "Plain Text"`, fait établi en Phase 13) n'a aucun
        signal d'ordre exploitable dans cette réponse : il est laissé après
        tous les fragments positionnés, dans l'ordre où le moteur l'a rendu
        (tri Python stable) — **pas un ordre garanti correct**, seulement
        celui, non fiable, que le moteur produit dans ce cas. Documenté plutôt
        que masqué : mieux vaut un ordre visiblement non retrié qu'un ordre
        inventé qui semblerait correct sans l'être.
        """
        data = await self._call(
            "GET",
            f"/datasets/{dataset.external_id}/documents/{document.external_id}/chunks",
            params={"page_size": str(page_size), "page": str(page)},
        )
        parsed = self._parse(_RagflowChunkListData, data, "liste des fragments du document")
        ordered_chunks = sorted(
            parsed.chunks,
            key=lambda chunk: (
                (order_key := _document_order_key(chunk.positions)) is None,
                order_key or (0, 0.0),
            ),
        )
        chunks = [
            EngineChunk(
                chunk_id=chunk.id,
                content=normalize_chunk_whitespace(chunk.content),
                positions=_chunk_positions_of(chunk.positions),
                important_keywords=chunk.important_keywords,
                questions=chunk.questions,
                available=chunk.available,
            )
            for chunk in ordered_chunks
        ]
        return EngineDocumentChunksPage(
            chunks=chunks,
            document=EngineDocumentChunksMeta(
                chunk_method=parsed.doc.chunk_method,
                chunk_count=parsed.doc.chunk_count,
                token_count=parsed.doc.token_count,
            ),
            total=parsed.total,
            page=page,
            page_size=page_size,
        )

    async def aclose(self) -> None:
        """Ferme le client HTTP si l'adaptateur en est propriétaire."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ─── Plomberie HTTP ──────────────────────────────────────────────────────

    def _http_client(self) -> httpx.AsyncClient:
        """Renvoie le client, créé paresseusement au premier appel réseau."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{self._base_url}{API_PREFIX}",
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        return self._client

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, JsonValue] | None = None,
        params: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes]] | None = None,
    ) -> JsonValue:
        """Exécute une requête et traduit son issue — **`code` d'abord, statut ensuite**.

        RAGFlow répond quasi systématiquement `200 OK`, y compris sur échec
        métier : le seul indicateur fiable est `code` dans le corps. Le statut
        HTTP ne sert que de repli, pour les cas où aucun corps JSON exploitable
        n'est renvoyé (authentification refusée par le framework, panne du
        reverse proxy…).
        """
        try:
            response = await self._http_client().request(
                method,
                path,
                json=dict(json_body) if json_body is not None else None,
                params=dict(params) if params is not None else None,
                files=dict(files) if files is not None else None,
            )
        except httpx.HTTPError as error:
            raise EngineTransientError(f"Moteur d'ingestion injoignable : {error}.") from error

        try:
            body = response.json()
        except ValueError as error:
            raise self._error_for_status(response.status_code) from error
        if not isinstance(body, dict):
            raise self._error_for_status(response.status_code)

        code = body.get("code")
        if not isinstance(code, int):
            # Réponse hors contrat RAGFlow (page d'erreur du serveur, 401 émis
            # par le framework avant d'entrer dans la vue…) : on retombe alors,
            # et alors seulement, sur le statut HTTP.
            raise self._error_for_status(response.status_code)
        if code != 0:
            raise self._error_for_code(code, body.get("message"))
        return body.get("data")

    @staticmethod
    def _failure_detail(progress_msg: str) -> str | None:
        """Dernière ligne du journal d'un document en échec, requalifiée si la cause est connue."""
        line = last_progress_line(progress_msg)
        return actionable_engine_message(line) if line is not None else None

    @staticmethod
    def _error_for_code(code: int, message: object) -> EngineError:
        """Traduit le `code` du corps en sous-classe d'erreur du port.

        `102` (« introuvable ou interdit ») reste une `EngineError` générique :
        RAGFlow rend les deux cas indiscernables, et RAGEngine connaît déjà
        l'existence de ses propres objets par sa base — l'ambiguïté du moteur ne
        doit pas fuir jusqu'à l'utilisateur.

        Le service d'inférence injoignable **n'obtient pas de sous-classe à lui**,
        et ce n'est pas un raccourci : il revient en `code=100`, donc déjà en
        `EngineTransientError`, dont la sémantique — passager, rejouable, `503` —
        décrit exactement la situation (redémarrer la pile GPU suffit). Une
        sous-classe supplémentaire ne changerait ni le statut, ni l'éligibilité au
        retry, ni la façon dont un appelant réagit : seul le message manquait.
        """
        detail = actionable_engine_message(
            str(message) if message else "Le moteur d'ingestion a rejeté la requête."
        )
        extensions: dict[str, JsonValue] = {"engine_code": code}
        if code in _VALIDATION_CODES:
            return EngineValidationError(detail, extensions=extensions)
        if code in _TRANSIENT_CODES:
            return EngineTransientError(detail, extensions=extensions)
        return EngineError(detail, extensions=extensions)

    @staticmethod
    def _error_for_status(status_code: int) -> EngineError:
        """Repli sur le statut HTTP quand le corps ne porte pas de `code` exploitable."""
        detail = f"Réponse hors contrat du moteur d'ingestion (HTTP {status_code})."
        extensions: dict[str, JsonValue] = {"status_code": status_code}
        if status_code >= 500:
            return EngineTransientError(detail, extensions=extensions)
        return EngineError(detail, extensions=extensions)

    @staticmethod
    def _require_str(payload: JsonValue, key: str, operation: str) -> str:
        """Extrait une chaîne obligatoire d'un objet de réponse, ou échoue clairement."""
        if isinstance(payload, dict):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        message = f"Réponse illisible du moteur d'ingestion ({operation}) : `{key}` absent."
        raise EngineError(message)

    @staticmethod
    def _parse[T: BaseModel](model: type[T], payload: JsonValue, operation: str) -> T:
        """Valide une portion de réponse, en traduisant l'échec en erreur de port."""
        try:
            return model.model_validate(payload)
        except PydanticValidationError as error:
            raise EngineError(f"Réponse illisible du moteur d'ingestion ({operation}).") from error

    def _first_document(self, data: JsonValue, document: EngineDocumentRef) -> _RagflowDocument:
        """Extrait l'unique document de la liste filtrée par identifiant.

        RAGFlow n'offre pas d'endpoint de métadonnées par identifiant : la seule
        lecture d'état possible est `GET /datasets/{id}/documents?id={doc}`, dont
        la réponse est `{"total": n, "docs": [...]}`.
        """
        docs: Any = data.get("docs") if isinstance(data, dict) else None
        if not isinstance(docs, list) or not docs:
            raise EngineError(
                f"Le moteur d'ingestion ne connaît pas le document {document.external_id}."
            )
        return self._parse(_RagflowDocument, docs[0], "état du document")
