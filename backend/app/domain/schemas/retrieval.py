"""Schémas du retrieval — `POST /api/v1/retrieval/search` et `/retrieval/chat` (Schéma B).

`RetrievalFilters` factorise les champs de filtrage communs aux deux endpoints,
tous deux **racines** (CONTRIBUTING.md §4) : la base de connaissances arrive dans le
corps (`knowledge_base_id`), pas dans le chemin — `RetrievalService` résout
donc lui-même l'accès via `PermissionService`, sans passer par
`require_kb_permission` (qui suppose un `kb_id` de chemin).

La réponse de recherche est une **enveloppe** (`RetrievalSearchResponse`)
plutôt qu'un tableau JSON nu en racine : elle laisse la place à des métadonnées
futures (facettes, temps de recherche…) sans rupture de contrat `/v1`. Le chat
(Phase 7) n'a pas d'enveloppe de réponse : sa réponse est un flux
`text/event-stream`, décrit dans `app/api/v1/routes/retrieval.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from app.domain.schemas.common import (
    MAX_PROVIDER_ID_LENGTH,
    MAX_THEME_LENGTH,
    InputModel,
    ReadModel,
)
from app.providers.engine.base import MAX_SEARCH_RESULTS

DEFAULT_CHAT_MAX_TOKENS: Final = 1024
"""Plafond de génération par défaut du chat — raisonnable pour une réponse groundée courte."""

MAX_CHAT_MAX_TOKENS: Final = 4096
"""Borne haute de `RetrievalChatRequest.max_tokens` — pas de génération illimitée."""

# --- Bornes de taille des textes envoyés au GPU et au LLM (ADR-053) ----------
# Longueurs en caractères (`len` Python, après `strip`). Le frontend applique
# les mêmes valeurs (`frontend/src/api/text-limits.ts`) : toute modification ici
# doit y être reportée, sans quoi une conversation légitime finirait en `422`.

MAX_QUERY_LENGTH: Final = 4_000
"""Requête de recherche, et tout tour `user` du chat (qui peut devenir la requête).

~800 tokens BGE-M3 (≈ 5 caractères par token sur le corpus français : le
fragment de 41 264 caractères de l'incident du 2026-09-06 en comptait ~8 000),
soit dix fois moins que la troncature de TEI (8 192 tokens). La requête est
appariée à **chacun** des candidats du reranking : sa longueur se paie autant
de fois qu'il y a de candidats, et c'est ce produit — pas la seule entrée du
modèle d'embedding — que la borne tient au niveau d'un fragment ordinaire. Une
page A4 de texte en fait ~3 000 : une question, même détaillée ou collée avec
son contexte, tient dessous."""

MAX_ASSISTANT_TURN_LENGTH: Final = 32_000
"""Tour `assistant` renvoyé par le client dans l'historique.

C'est une réponse que la façade a elle-même générée, plafonnée à
`MAX_CHAT_MAX_TOKENS` (4 096 tokens, ~16 400 caractères à ~4 caractères par
token) : la borne en tolère près du double, pour qu'aucune réponse réelle,
rejouée telle quelle, ne soit refusée. Le client tronque de toute façon un tour
plus long avant l'envoi."""

MAX_CHAT_MESSAGES: Final = 40
"""Nombre de tours de l'historique (20 échanges). Le client garde les plus récents."""

MAX_CHAT_TRANSCRIPT_LENGTH: Final = 64_000
"""Somme des contenus de l'historique (~16 000 tokens transmis au LLM à chaque tour).

Borne la dépense par tour et la taille du prompt, sous le contexte des modèles
du catalogue ; le client retire les tours les plus anciens pour s'y tenir."""

MAX_FILTER_DOCUMENT_IDS: Final = 1_000
"""Documents cités par `document_ids` : bien au-delà d'une sélection faite à la main."""

MAX_FILTER_VALUES: Final = 100
"""Valeurs d'un filtre descriptif (`languages`, `content_types`, `themes`)."""

MAX_LANGUAGE_CODE_LENGTH: Final = 35
"""Plus long étiquetage de langue BCP 47 d'usage courant (RFC 5646 §4.4.1)."""

MAX_CONTENT_TYPE_LENGTH: Final = 255
"""`type/sous-type` : 127 caractères chacun au plus (RFC 6838 §4.2)."""

LanguageCode = Annotated[str, Field(max_length=MAX_LANGUAGE_CODE_LENGTH)]
ContentType = Annotated[str, Field(max_length=MAX_CONTENT_TYPE_LENGTH)]
Theme = Annotated[str, Field(max_length=MAX_THEME_LENGTH)]


class RetrievalFilters(InputModel):
    """Champs de filtrage communs à `/retrieval/search` et `/retrieval/chat`.

    `query` reste propre à la recherche (`RetrievalSearchRequest`) : le chat
    dérive sa requête du dernier tour utilisateur de `RetrievalChatRequest.messages`,
    il n'y a donc rien d'équivalent à factoriser ici pour ce champ.
    """

    knowledge_base_id: uuid.UUID = Field(description="Base de connaissances interrogée.")
    top_k: int = Field(
        default=10,
        ge=1,
        le=MAX_SEARCH_RESULTS,
        description="Nombre de fragments à rendre — borné par la limite réelle du moteur.",
    )
    rerank: bool | None = Field(
        default=None,
        description=(
            "Active le reranking natif RAGFlow, via le reranker configuré sur la "
            "base (`KnowledgeBase.reranker_model_id`). `true`/`false` explicites "
            "sont toujours respectés tels quels. `None` (par défaut, valeur non "
            "envoyée) laisse `RetrievalService` décider selon la base — "
            "**pas** « pas de reranking » : le service active le reranking "
            "automatiquement si un reranker est configuré sur la base, sinon il "
            "reste inactif (ADR-026). Un `rerank=true` explicite reste refusé "
            "(422) si la base n'en déclare aucun."
        ),
    )
    similarity_threshold: float | None = Field(
        default=None,
        description=(
            "Seuil de similarité transmis tel quel au moteur ; `None` laisse "
            "RAGFlow appliquer le sien. **Garde-fou** (ADR-012 §4, ADR-026) : "
            "activer le reranking (effectif, après résolution du défaut "
            "`rerank`) change l'échelle du score combiné — un seuil non nul "
            "combiné à un reranking effectif est refusé (422) sauf accusé de "
            "réception explicite (`acknowledge_threshold_risk=true`), la chute "
            "de recall@5 mesurée (0,889 → 0,593, Phase 12 Étape 2) n'étant pas "
            "un risque à encourir sans le savoir."
        ),
    )
    acknowledge_threshold_risk: bool = Field(
        default=False,
        description=(
            "Accusé de réception explicite du risque documenté sur "
            "`similarity_threshold` : combiner un reranking effectif à un "
            "seuil de similarité non nul (`!= 0.0`) est refusé (422) sauf à "
            "poser ce champ à `true`. Champ fermé, au même titre que "
            "`PrecheckDecision` (ADR-012) : ne couvre que ce risque précis, "
            "jamais un contournement générique des validations."
        ),
    )
    document_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=MAX_FILTER_DOCUMENT_IDS,
        description=(
            "Identifiants **RAGEngine** de documents de la base ciblée, traduits "
            "en interne vers leurs identifiants RAGFlow avant l'appel au moteur. "
            "`None` ne filtre rien. Un identifiant qui ne correspond à aucun "
            "document de cette base est une erreur (422) : c'est une citation "
            "explicite de l'appelant, jamais ignorée en silence."
        ),
    )
    languages: list[LanguageCode] | None = Field(
        default=None,
        max_length=MAX_FILTER_VALUES,
        description=(
            "Codes ISO 639-1 (ADR-003) : un document correspond si "
            "`detected_languages` recoupe au moins un des codes donnés. `None` "
            "ou liste vide ne filtrent rien. Contrairement à `document_ids`, "
            "une combinaison qui n'exclut aucun document en erreur : c'est un "
            "filtre descriptif, pas la citation d'objets précis — l'absence de "
            "correspondance rend une liste vide, exactement comme une "
            "recherche dont la requête ne trouve rien."
        ),
    )
    content_types: list[ContentType] | None = Field(
        default=None,
        max_length=MAX_FILTER_VALUES,
        description=(
            "Types MIME (`Document.content_type`) à retenir, par exemple "
            "`application/pdf`. `None` ou liste vide ne filtrent rien. Même "
            "sémantique que `languages` : un filtre descriptif, jamais une "
            "erreur s'il n'exclut aucun document."
        ),
    )
    ingested_after: datetime | None = Field(
        default=None,
        description=(
            "Ne retient que les documents créés à cet instant (UTC) ou après. "
            "Même sémantique que `languages`/`content_types`."
        ),
    )
    ingested_before: datetime | None = Field(
        default=None,
        description=(
            "Ne retient que les documents créés strictement avant cet instant "
            "(UTC). Même sémantique que `languages`/`content_types`."
        ),
    )
    themes: list[Theme] | None = Field(
        default=None,
        max_length=MAX_FILTER_VALUES,
        description=(
            "Étiquettes de thème (`Document.theme`), saisies par l'utilisateur "
            "plutôt que déduites — un document correspond si son thème figure "
            "dans la liste donnée. `None` ou liste vide ne filtrent rien. Même "
            "sémantique descriptive que `languages` : une combinaison qui "
            "n'exclut aucun document ne lève jamais d'erreur."
        ),
    )


class RetrievalSearchRequest(RetrievalFilters):
    """Requête de recherche hybride sur une base de connaissances."""

    query: str = Field(
        min_length=1,
        max_length=MAX_QUERY_LENGTH,
        description=(
            "Texte de la requête, non vide après strip. Borné : la requête est "
            "embeddée puis appariée à chaque candidat du reranking sur le GPU."
        ),
    )


class RetrievalResult(ReadModel):
    """Un fragment de résultat, avec le document RAGEngine résolu."""

    chunk_id: str
    document_id: uuid.UUID = Field(
        description="Identifiant RAGEngine du document, résolu depuis sa référence moteur."
    )
    filename: str
    content: str
    similarity: float
    vector_similarity: float
    term_similarity: float
    page_start: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Première page du document couverte par le fragment (1-based), ou "
            "`null` quand le moteur ne localise pas le fragment de façon fiable "
            "— cas des formats sans pagination (Word, texte…), où la position "
            "rendue n'est qu'un rang de fragment. `null` signifie « position "
            "inconnue » et jamais « page 1 »."
        ),
    )
    page_end: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Dernière page couverte, égale à `page_start` pour un fragment "
            "tenant sur une seule page. Toujours `null` en même temps que "
            "`page_start` : un fragment chevauche souvent plusieurs pages."
        ),
    )


class RetrievalSearchResponse(ReadModel):
    """Enveloppe de réponse — laisse la place à des métadonnées futures."""

    results: list[RetrievalResult] = Field(default_factory=list)


class ChatTurn(InputModel):
    """Tour de conversation soumis par le client — jamais `"system"`, injecté serveur."""

    role: Literal["user", "assistant"]
    content: str = Field(
        min_length=1,
        max_length=MAX_ASSISTANT_TURN_LENGTH,
        description=(
            "Contenu du tour, non vide après strip. Un tour `user` est borné comme "
            f"une requête de recherche ({MAX_QUERY_LENGTH} caractères) — il peut en "
            f"devenir une ; un tour `assistant` à {MAX_ASSISTANT_TURN_LENGTH}."
        ),
    )

    @field_validator("content")
    @classmethod
    def _user_turn_is_bounded_like_a_query(cls, value: str, info: ValidationInfo) -> str:
        """Un tour `user` peut servir tel quel de requête de recherche (1er tour, repli).

        Borné ici plutôt qu'à la construction de `RetrievalSearchRequest` dans le
        service, où le dépassement serait une erreur `500` au lieu d'un `422`.
        Même type d'erreur que `max_length` (`string_too_long`), pour qu'un
        client n'ait qu'un cas à reconnaître.
        """
        if info.data.get("role") == "user" and len(value) > MAX_QUERY_LENGTH:
            raise PydanticCustomError(
                "string_too_long",
                "String should have at most {max_length} characters",
                {"max_length": MAX_QUERY_LENGTH},
            )
        return value


def _validate_chat_messages(value: list[ChatTurn]) -> list[ChatTurn]:
    """Invariants d'un historique soumis, communs au Playground et au chatbot publié.

    Portés par le type plutôt que par chaque requête : `PublicChatbotRequest` est
    recopié dans une `RetrievalChatRequest` par le routeur, et un invariant
    absent du premier y lèverait une erreur de validation hors de la phase de
    requête — un `500`, pas un `422`.
    """
    if value[-1].role != "user":
        raise ValueError('La conversation doit se terminer par un tour "user".')
    length = sum(len(turn.content) for turn in value)
    if length > MAX_CHAT_TRANSCRIPT_LENGTH:
        raise PydanticCustomError(
            "chat_transcript_too_long",
            "Conversation should have at most {max_length} characters in total, got {length}",
            {"max_length": MAX_CHAT_TRANSCRIPT_LENGTH, "length": length},
        )
    return value


ChatMessages = Annotated[
    list[ChatTurn],
    Field(min_length=1, max_length=MAX_CHAT_MESSAGES),
    AfterValidator(_validate_chat_messages),
]
"""Historique de conversation borné — nombre de tours, taille totale, dernier tour `user`."""


class RetrievalChatRequest(RetrievalFilters):
    """Requête du chat génératif adossé au retrieval (`POST /api/v1/retrieval/chat`).

    La réponse n'est **pas** un schéma Pydantic : c'est un flux SSE
    (`text/event-stream`), assemblé par le routeur à partir de
    `RetrievalService.chat` — voir `app/api/v1/routes/retrieval.py`.
    """

    messages: ChatMessages = Field(
        description=(
            "Historique de conversation, sans tour `system` (injecté serveur). "
            "Doit se terminer par un tour `user` : son contenu sert de requête "
            "pour la recherche hybride sous-jacente, complété par l'historique "
            "récent quand il ne se suffit pas à lui-même (deuxième tour ou plus). "
            f"Au plus {MAX_CHAT_MESSAGES} tours et {MAX_CHAT_TRANSCRIPT_LENGTH} "
            "caractères au total : un client garde les tours les plus récents."
        ),
    )
    llm_provider_id: str = Field(
        min_length=1,
        max_length=MAX_PROVIDER_ID_LENGTH,
        description=(
            "Identifiant du modèle de génération (catalogue réel via "
            "`GET /api/v1/providers`, champ `llm`). Obligatoire : le choix du "
            "modèle de génération est une décision produit explicite, jamais un "
            "repli silencieux."
        ),
    )
    max_tokens: int = Field(
        default=DEFAULT_CHAT_MAX_TOKENS,
        ge=1,
        le=MAX_CHAT_MAX_TOKENS,
        description="Plafond de tokens générés, transmis tel quel à `LLMPort.stream`.",
    )
