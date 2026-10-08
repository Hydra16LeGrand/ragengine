"""Traduction d'une décision de precheck en profil de chunking du moteur.

Fonction **pure**, sans I/O ni dépendance au moteur concret : elle produit un
`ChunkingProfile`, c'est-à-dire un objet du port, jamais un payload RAGFlow.
C'est ici, et nulle part ailleurs, que se lit la correspondance entre les
options montrées à l'utilisateur (`PrecheckDecision`, catalogue fermé) et les
paramètres du moteur (`docs/ingestion-engine-architecture.md` §4).

Les deux options sont **indépendantes et cumulables** : la seconde (`heavy_ocr`)
règle toujours le même paramètre, quel que soit le format. La première
(`optimize_for_tables`) **dépend du format du document** — fait découvert en
test de bout en bout, pas anticipé par l'audit initial : `chunk_method=table`
échoue sur un PDF (`file type not supported yet(excel, text, csv supported)`,
message RAGFlow verbatim). Il n'est donc appliqué que sur un tableur ; sur un
PDF, l'intention se traduit par le même renforcement de l'analyse de mise en
page que `heavy_ocr` — c'est déjà DeepDOC, via `naive`, qui reconnaît les
tableaux dans un PDF, pas un template dédié.

**`layout_recognize` dépend désormais du résultat du precheck** (ADR-023 §3,
Étape 5 de la Phase 11 du plan interne) : mesuré sur le corpus de 29
documents (matrice `chunk_method`/`parser_config`, Étape 2), `Plain Text` bat
`DeepDOC` sur tout document dont le precheck n'a pas détecté de scan
(`is_likely_scanned = false`) — moins d'erreurs de casse, moins d'entrelacement
de colonnes, meilleure couverture du texte source. `DeepDOC` reste le choix par
défaut sur un document probablement scanné, et `heavy_ocr`/`optimize_for_tables`
(hors tableur) gardent la priorité sur ce nouveau défaut : `Plain Text` saute
entièrement l'analyse de mise en page, ce qui rend impossible la détection de
tableaux (fait établi 8, plan interne).

**Le format image court-circuite toutes les options**, pour la même raison
empirique. Le moteur choisit `picture` de lui-même à l'upload et refuse ensuite
tout `chunk_method` sur un document visuel (`Not supported yet!`) : lui en
envoyer un — fût-ce `picture`, fût-ce le `naive` par défaut — fait échouer le
document entier avant même le premier fragment. Le seul profil qui marche est le
profil vide, et il OCRise déjà au maximum. `use_parent_child` n'a aucun sens
pour un chunking `picture` (pas de notion de fragments enfants/parents sur un
OCR mono-bloc) et n'est, par prudence, jamais envoyé sur ce format — cohérent
avec le reste du profil vide.

**Le format diaporama court-circuite lui aussi toutes les options**, pour la
même raison et sur le même modèle. La garde du moteur ne porte pas que sur les
documents visuels : `api/utils/validation_utils.py::validate_chunk_method`
(la fonction à deux arguments, pas le validateur Pydantic homonyme) rejette
`Not supported yet!` dès que la requête porte une clé `chunk_method` **et** que
le nom du fichier finit par `.ppt`/`.pptx`/`.pages` — quelle que soit la valeur
envoyée, y compris `presentation`, celui que le moteur s'assigne lui-même au
téléversement (`FileService.get_parser`). Et le contrôle se déclenche sur la
seule **présence** de la clé (`if "chunk_method" in req`,
`api/apps/services/document_api_service.py`), pas sur son contenu. Mesuré sur
le document 31 du corpus : poser `chunk_method=naive` fait échouer le document
avant tout fragment, ne rien poser en rend 4, propres. Le `parser_config` ne
rachète rien au passage — `rag/app/presentation.py::chunk()` ne le lit que sur
la branche `.pdf`, jamais sur `.pptx` —, d'où le même profil entièrement vide
que pour une image.

**Un `.eml` court-circuite lui aussi toutes les options** (ADR-044 §5.4), mais
par le **nom de fichier**, pas par `PrecheckFormat` : contrairement au
diaporama, `.eml` n'a pas de valeur dédiée dans l'énumération — il tombe dans
`UNSUPPORTED`, au même titre que `.docx`/`.md`, qui eux n'ont pas cette
contrainte moteur (précheck ne les analyse pas non plus, mais le moteur
accepte `naive` dessus sans broncher). Mesuré en direct : poser
`chunk_method=naive` échoue avec le même message que le diaporama (« file type
not supported yet(pdf, xlsx, doc, docx, txt supported) ») ; ne rien poser
laisse le moteur choisir lui-même son propre template `email` et rend 2
fragments propres. D'où le paramètre `filename` de `profile_for_decision`,
absent des autres branches : c'est le seul cas où le format precheck ne
suffit pas à distinguer un document qui a cette contrainte d'un autre qui ne
l'a pas.

**Limite connue et non contournable : le libellé de feuille d'un `.xlsx` est
perdu par le template `table`.** Mesuré (Étape 2, Phase 15, documents 08 et 52)
: `naive` répète le nom de la feuille en queue de chaque ligne exportée
(`… ——Budget fictif`, produit par `deepdoc/parser/excel_parser.py`), `table` ne
le fait pas — sur un classeur multi-feuilles, deux lignes de feuilles
différentes deviennent indiscernables. Aucun paramètre du moteur ne permet de le
réinjecter : `rag/app/table.py::chunk()` ne lit de `parser_config` que
`table_column_mode` et `table_column_roles` (rôles de colonnes), et
`Excel.__call__` empile un `DataFrame` par feuille **sans jamais transporter son
nom** jusqu'au fragment. Rien n'est donc câblé ici : fabriquer le libellé nous-
mêmes voudrait dire pré-traiter le classeur avant de le confier au moteur, c'est-
à-dire réimplémenter sa brique (CONTRIBUTING.md §3.3). Le contournement disponible est
un **choix de l'utilisateur**, pas un routage : décocher « Optimiser pour les
tableaux » retombe sur `naive`, qui garde le libellé au prix d'un fragment par
paquet de lignes au lieu d'un par ligne. Le `.csv` n'est pas concerné —
`excel_parser.py` n'y étiquette rien, et il n'a pas de feuilles.

**Docling (ADR-043) raffine la branche « analyse de mise en page », il ne
s'ajoute pas au catalogue montré à l'utilisateur.** Sur un PDF non numérisé
dont le precheck a vu **à la fois** des colonnes et des tableaux, l'analyse
complète est servie par `Docling` au lieu de `DeepDOC`. Le routage est
**automatique et silencieux**, et c'est un choix, pas une facilité :

1. ce n'est pas une option de plus, c'est une meilleure exécution d'une option
   que l'utilisateur coche déjà (« Analyser la mise en page ») — l'architecture
   d'ADR-031 lui fait arbitrer des *intentions*, pas des noms de moteurs ;
2. il n'y a **aucune régression de latence** face au `DeepDOC` qu'il remplace
   (325 s contre 361 s mesurées sur `real-notice-2042.pdf`, 41 pages) : le seul
   arbitrage que l'utilisateur pourrait vouloir refaire n'existe pas ;
3. ajouter une quatrième case demanderait précisément l'arbitrage qu'ADR-043
   supprime — « ordre de lecture *ou* tableaux », alors que ce profil rend les
   deux (`column_switch_rate` 0,023 pour une borne entrelacée à 0,167,
   15 tableaux HTML sur 21, 100 % de fragments positionnés).

La contrepartie est dite à l'écran de precheck plutôt que cachée : le texte
d'aide qui exposait l'arbitrage quand colonnes et tableaux coexistent
(`precheck.decision.ocrColumnsWithTables`) annonce désormais que ce document
reçoit le moteur qui tient les deux.

**Le sens de la garde `is_likely_scanned` est inchangé et prime toujours** : un
document probablement numérisé reçoit `DeepDOC`, jamais `Docling`. Ce n'est pas
de la prudence décorative — l'OCR de Docling porte le défaut qu'ADR-030 a
corrigé pour DeepDOC (RapidOCR chargé sur un modèle de reconnaissance chinois,
vérifié dans les journaux du conteneur). Le routage Docling ne vise que des
documents nativement numériques, où l'OCR ne se déclenche pas.

**`task_page_size` accompagne toujours `Docling`, et n'a de sens que pour lui.**
`by_docling()` reçoit `from_page`/`to_page` et **ne les transmet pas** à
`DoclingParser.parse_pdf()`, qui n'a pas de paramètre de plage (ADR-043,
défaut n°1). RAGFlow découpant tout PDF en tâches de 12 pages
(`api/db/services/task_service.py`), chaque tâche reconvertirait et
ré-embedderait le **document entier** : latence en O(pages²), mesurée (un PDF de
121 pages converti 10 fois, à ~142 s la conversion). Forcer une taille de tâche
supérieure au nombre de pages ramène le document à une tâche unique — 377,7 s à
325,5 s sur 41 pages, et l'écart explose avec la longueur. Le jour où l'amont
transmettra les bornes, cette clé doit être **retirée**, pas laissée en place.

**`use_parent_child` (Phase 13) est additif**, posé dans `parser_config`
indépendamment du routage `layout_recognize`/`chunk_method` ci-dessus
(ADR-023) — jamais mélangé à sa logique de décision, seulement ajouté au
dictionnaire déjà produit par elle.

**`chunk_method_override` (Phase 15) est substitutif, et ne substitue qu'une
chose** : le template. Il est lu une seule fois, en tête, puis sert de valeur de
remplacement aux deux branches qui produisent un `chunk_method` — le calcul de
`layout_recognize` (y compris le raffinement Docling) et celui des clés
additives ne le voient jamais. Deux conséquences voulues :

1. la sélection fine et l'analyse de mise en page restent **cumulables** —
   forcer `laws` sur un PDF n'annule pas « Analyser la mise en page », ce sont
   deux décisions distinctes que l'écran d'aiguillage présente séparément ;
2. les deux formats court-circuités plus haut (image, diaporama) l'**ignorent**
   par construction, puisqu'ils rendent leur profil avant de l'avoir lu. C'est
   le comportement voulu et non un effet de bord : le moteur y refuse tout
   `chunk_method`, honorer la consigne ferait échouer le document au lieu de
   l'indexer. Le filtrage par document vit dans l'interface, qui ne propose
   jamais le sélecteur sur ces formats-là.

Le catalogue des valeurs acceptées est fermé et documenté valeur par valeur
dans `app/domain/schemas/document.py::AdvancedChunkMethod` — c'est là, et non
ici, que se lit *pourquoi* `email`, `book`, `one`, `tag` et `resume` n'y sont
pas.

**Bug moteur découvert en test de bout en bout, contourné ici (pas anticipé
par l'étude d'impact)** : `PATCH /datasets/{id}/documents/{doc}` — le seul
endpoint document utilisé par RAGEngine — valide `parser_config` contre
`ParserConfig` (`extra="forbid"`) puis persiste **le dict brut reçu**, sans
jamais le faire passer par l'aplatissement `parent_child.use_parent_child` →
`children_delimiter` que fait `get_parser_config()` (utilisé par le chemin
*dataset*, `api/apps/services/dataset_api_service.py`, mais **pas** par le
chemin *document*, `api/apps/restful_apis/document_api.py`). Or c'est
uniquement la clé **plate** `parser_config.children_delimiter` que lit
`rag/app/naive.py::chunk()` au moment du chunking — poser seulement
`parser_config.parent_child.use_parent_child=true` sur un document (comme le
ferait une lecture naïve du schéma `ParentChildConfig`) est donc un **no-op
silencieux** sur cette version : validé, persisté, mais jamais lu par
l'exécution. Vérifié par ingestion réelle : chunks classiques, aucun `mom_id`,
`degenerate_content` sans objet puisqu'il n'y a rien à découper.

Le contournement, vérifié par une seconde ingestion réelle (mêmes chunks
courts marqués `available: false` — les fragments « mère » — et longs
`available: true` retrouvés par la recherche, comportement `parent_child`
correct) : poser la clé plate via `parser_config.ext` — champ libre de
`ParserConfig` (`Field(default={})`, sans restriction de sous-clés), que
**les deux chemins**, document et dataset, fusionnent tel quel dans le
`parser_config` persisté (`req["parser_config"].update(update_doc_req.
parser_config.ext)`, `api/apps/restful_apis/document_api.py`). Envoyé aux
côtés de `parent_child.use_parent_child=true` (schéma correct, sans effet sur
cette version mais qui deviendrait la voie normale si RAGFlow corrige
l'aplatissement document un jour — aucun conflit entre les deux formes).
"""

from __future__ import annotations

from typing import Final

from pydantic import JsonValue

from app.domain.schemas.document import PrecheckDecision
from app.ingestion.precheck.schemas import PrecheckFormat
from app.providers.engine.base import DEFAULT_CHUNK_METHOD, ChunkingProfile

NO_CHUNK_METHOD: Final = None
"""Absence assumée de consigne de template — le seul profil qu'une image ou un diaporama accepte.

Nommé plutôt que laissé en `None` littéral parce que ce n'est pas un oubli : le
moteur assigne **lui-même** son template au téléversement (fait vérifié :
`POST /datasets/{id}/documents` renvoie déjà `chunk_method="picture"` pour une
image, `"presentation"` pour un `.pptx`), et il **refuse ensuite qu'on lui en
impose un**, y compris le sien — `PUT /datasets/{id}/documents/{doc}` répond
`Not supported yet!` dès que la requête porte un `chunk_method`, quelle qu'en
soit la valeur. Une seule garde couvre les deux cas (source lue dans l'instance
déployée, `api/utils/validation_utils.py::validate_chunk_method`) :
`doc.type == FileType.VISUAL or re.search(r"\\.(ppt|pptx|pages)$", doc.name)`.
Ne rien envoyer est donc le seul chemin qui laisse ces documents se faire
extraire puis indexer — vérifié de bout en bout pour les deux familles.
"""

TABLE_CHUNK_METHOD: Final = "table"
"""Template tabulaire : un chunk = une ligne de données (fait vérifié en audit).

**Réservé aux tableurs** (`.xlsx`/`.csv`) : RAGFlow le refuse explicitement sur
un PDF (fait vérifié en test de bout en bout, pas une hypothèse). Deux réserves
produit s'appliquent quand il est utilisable : ce parseur infère des types et
peut réinterpréter une valeur textuelle courte ambiguë (« T1 » lu comme une
date), et il **perd le libellé de feuille** d'un classeur multi-feuilles, sans
aucun paramètre moteur pour le réinjecter (§ docstring de module).
"""

LAYOUT_RECOGNIZE_KEY: Final = "layout_recognize"
"""Clé de `parser_config` qui pilote l'analyse de mise en page du moteur."""

HEAVY_OCR_LAYOUT_RECOGNIZE: Final = "DeepDOC"
"""Valeur imposée par `heavy_ocr` (« Analyser la mise en page »), vérifiée sur l'instance réelle.

RAGFlow n'a qu'un moteur d'extraction visuelle local, DeepDOC ; les autres
valeurs acceptées désignent soit son court-circuit (`Plain Text`, qui saute
l'analyse de mise en page), soit des parseurs externes exigeant un fournisseur
déclaré, dont RAGEngine n'en configure aucun. « OCR lourd » consiste donc à
**imposer** DeepDOC plutôt qu'à hériter du réglage du dataset : le document est
analysé au maximum de ce que le moteur sait faire, quoi qu'ait été configuré
en amont. Contrôlé contre l'instance : la valeur est acceptée et relue telle
quelle après le `PATCH` du document.
"""

DOCLING_LAYOUT_RECOGNIZE: Final = "Docling"
"""Troisième moteur de mise en page (ADR-043), embarqué dans l'image dérivée.

Valeur relue telle quelle par le moteur : `rag/app/naive.py` fait
`PARSERS.get(layout_recognize.strip().lower())`, et `PARSERS["docling"]` existe
déjà en amont — aucun code moteur n'est touché, seule l'installation du paquet
`docling==2.71.0` au build distingue notre image (`docker/ragflow-cpu.Dockerfile`).

Réservé au cas mesuré : PDF **non numérisé** portant **colonnes et tableaux**.
Ailleurs il ne rachète rien — `Plain Text` égale son ordre de lecture pour dix
fois moins cher sur un document sans tableau (0,021 contre 0,023 pour 31 s
contre 325 s), et un document mono-colonne n'a aucun grief d'ordre à corriger.
"""

TASK_PAGE_SIZE_KEY: Final = "task_page_size"
"""Clé de `parser_config` qui borne le découpage du document en tâches moteur.

Champ déclaré du `ParserConfig` de RAGFlow (`api/utils/validation_utils.py`,
`int | None`, `ge=1`) — donc accepté par le `PATCH` document, dont le schéma est
`extra="forbid"`. Posée **uniquement** avec `Docling` (cf. docstring de module) :
sur les deux autres profils, le découpage par tâches de 12 pages est le
comportement voulu du moteur et rien ne justifie de s'en écarter.
"""

DOCLING_SINGLE_TASK_PAGE_MARGIN: Final = 1
"""Marge ajoutée au nombre de pages pour garantir **une seule** tâche.

RAGFlow itère `range(0, pages, task_page_size)` : `task_page_size == pages`
suffirait déjà. La marge d'une page couvre l'écart d'un si le compte du precheck
et celui du moteur (`PdfParser.total_page_number`) divergeaient d'une unité —
deux lectures différentes du même fichier, jamais garanties identiques.
"""

DOCLING_SINGLE_TASK_PAGE_FALLBACK: Final = 100_000
"""Taille de tâche posée quand le nombre de pages est inconnu — `MAXIMUM_PAGE_NUMBER`.

Cas qui ne devrait pas se produire (le routage Docling exige
`two_column_page_count > 0`, donc un PDF réellement analysé, donc un
`page_count` connu), mais dont le défaut inverse serait catastrophique : sans
cette borne haute, une absence de compte retomberait sur une tâche par page,
c'est-à-dire exactement la latence en O(pages²) que cette clé existe pour
supprimer. La valeur est celle du plafond du moteur
(`common/constants.py::MAXIMUM_PAGE_NUMBER`), pas un grand nombre arbitraire.
"""

PARENT_CHILD_KEY: Final = "parent_child"
"""Clé de `parser_config` qui déclare l'intention (`ParentChildConfig`) — schéma correct,
mais **sans effet à elle seule** sur le chemin document de cette version du moteur
(§ docstring de module). Envoyée quand même, aux côtés de `PARENT_CHILD_EXT_KEY`."""

PARENT_CHILD_EXT_KEY: Final = "ext"
"""Champ libre de `ParserConfig`, seule voie confirmée pour faire lire au moteur la clé
plate `children_delimiter` sur le chemin document (§ docstring de module)."""

CHILDREN_DELIMITER_KEY: Final = "children_delimiter"
"""Clé plate lue par `rag/app/naive.py::chunk()` — celle qui déclenche réellement le
découpage parent/enfant, par opposition à `parent_child.children_delimiter` (ignorée sur
le chemin document)."""

DEFAULT_CHILDREN_DELIMITER: Final = "\n"
"""Même défaut que `ParentChildConfig.children_delimiter` côté moteur — aucune
personnalisation exposée à l'utilisateur, `use_parent_child` reste un simple booléen."""

PLAIN_TEXT_LAYOUT_RECOGNIZE: Final = "Plain Text"
"""Court-circuit de l'analyse de mise en page — nouveau défaut sur couche texte propre.

Mesuré en Étapes 1-2 de la Phase 11 (plan interne, fait établi 10) : sur un
document dont le precheck n'a pas détecté de scan, `Plain Text` produit moins
d'erreurs de casse, moins d'entrelacement de colonnes et une meilleure
couverture du texte source que `DeepDOC`. Réservé aux documents jugés propres :
il saute entièrement l'analyse de mise en page, ce qui rend impossible la
détection de tableaux (fait établi 8) — d'où la priorité laissée à `heavy_ocr`
et `optimize_for_tables` sur ce nouveau défaut (ADR-023 §3).

**Ce défaut est celui du contrat d'API, plus celui de l'interface** (ADR-031,
2026-08-11) : remesuré contre l'image dérivée portant l'OCR latin (ADR-030),
`DeepDOC` égale `Plain Text` en qualité de texte sur 5 documents sur 7 tout en rendant l'ordre
des fragments exploitable, donc l'écran d'aiguillage propose désormais
`heavy_ocr` coché sur tout PDF. Ce module ne change pas pour autant : un client
REST qui n'envoie pas la décision garde ce défaut-ci, et l'utilisateur peut
toujours décocher pour retomber ici. Seule exception intouchable, le
`or is_likely_scanned` de `profile_for_decision` — un scan reste routé vers
`DeepDOC` même décoché.
"""


def _additive_parser_config(decision: PrecheckDecision) -> dict[str, JsonValue]:
    """Options Phase 13, indépendantes du routage `layout_recognize`/`chunk_method`.

    N'ajoute rien si la décision est décochée — le défaut moteur
    (`use_parent_child=False`) est déjà celui voulu.
    """
    if not decision.use_parent_child:
        return {}
    return {
        PARENT_CHILD_KEY: {"use_parent_child": True},
        PARENT_CHILD_EXT_KEY: {CHILDREN_DELIMITER_KEY: DEFAULT_CHILDREN_DELIMITER},
    }


def _single_task_page_size(page_count: int | None) -> int:
    """Taille de tâche qui garantit une conversion Docling unique (§ docstring de module)."""
    if page_count is None or page_count <= 0:
        return DOCLING_SINGLE_TASK_PAGE_FALLBACK
    return page_count + DOCLING_SINGLE_TASK_PAGE_MARGIN


def _layout_analysis_is_served_by_docling(
    *,
    document_format: PrecheckFormat,
    two_column_page_count: int,
    table_count: int,
) -> bool:
    """Le cas mesuré d'ADR-043 : colonnes **et** tableaux sur un PDF numérique.

    Les deux signaux sont exigés ensemble, et le sont pour deux raisons
    distinctes : les colonnes parce que c'est là que `DeepDOC` se retourne
    contre le document (ordre de lecture entrelacé, ADR-042) ; les tableaux
    parce que sans eux `Plain Text` fait aussi bien pour dix fois moins cher —
    ADR-042 s'applique alors telle quelle, et rien ne change.

    Le contrôle de format est redondant avec `two_column_page_count`, toujours
    nul hors PDF (`precheck/columns.py`), et il est écrit quand même : ce
    routage n'a de sens que sur un PDF, et le dire ici évite qu'un changement
    futur du precheck le rende vrai ailleurs par effet de bord.
    """
    return document_format is PrecheckFormat.PDF and two_column_page_count > 0 and table_count > 0


def profile_for_decision(
    decision: PrecheckDecision,
    *,
    document_format: PrecheckFormat,
    is_likely_scanned: bool,
    filename: str = "",
    two_column_page_count: int = 0,
    table_count: int = 0,
    page_count: int | None = None,
) -> ChunkingProfile:
    """Projette l'aiguillage utilisateur et le precheck sur les paramètres du moteur (§4).

    `layout_recognize` suit désormais `is_likely_scanned` (ADR-023 §3) : un
    document probablement scanné reçoit `DeepDOC` (analyse complète, explicite
    plutôt qu'héritée du défaut moteur) ; un document à couche texte propre
    reçoit `Plain Text` (court-circuit, moins d'erreurs mesurées — fait établi
    10). `heavy_ocr` et `optimize_for_tables` (hors tableur) gardent la
    priorité sur ce défaut et forcent `DeepDOC` dans tous les cas : `Plain
    Text` saute l'analyse de mise en page nécessaire à la détection de
    tableaux (fait établi 8).

    Sur une **image** comme sur un **diaporama**, le profil est vide
    (`chunk_method = None`) quelles que soient les options : c'est le seul que
    le moteur accepte — une garde unique couvre les deux, cf. le docstring de
    module et `NO_CHUNK_METHOD`.

    **Le renforcement de mise en page se sert désormais en deux versions**
    (ADR-043) : `Docling` sur un PDF non numérisé qui porte colonnes **et**
    tableaux, `DeepDOC` partout ailleurs. Le raffinement ne touche que cette
    branche-là — la garde `is_likely_scanned` passe avant et impose toujours
    `DeepDOC`, et un document sans les deux signaux suit exactement le chemin
    d'ADR-023/ADR-042. `task_page_size` n'est posé qu'avec `Docling`.

    `use_parent_child` (Phase 13) s'ajoute au `parser_config` produit par la
    logique ci-dessus, sur toute image exclue (cf. `_additive_parser_config`)
    — jamais de logique conditionnelle propre, additif par construction.

    `chunk_method_override` (Phase 15) **remplace** le template retenu par les
    deux branches restantes, et rien d'autre : `layout_recognize` et les clés
    additives sont calculés exactement comme s'il était absent. Sur une image
    ou un diaporama il est ignoré — ces deux formats rendent leur profil avant
    de le lire, cf. le docstring de module. Idem sur un `.eml`, cf. plus bas.

    Args:
        decision: choix de l'utilisateur, relu depuis `Document.precheck_decision`.
        document_format: format détecté au precheck (`Document.precheck_report`) —
            arbitre si `optimize_for_tables` peut se traduire par `chunk_method=table`
            (tableur) ou doit retomber sur un renforcement de mise en page (PDF), et
            court-circuite tout pour une image ou un diaporama.
        is_likely_scanned: résultat de l'heuristique de precheck
            (`Document.precheck_report`) — arbitre `layout_recognize` en l'absence
            de `heavy_ocr`/`optimize_for_tables`.
        filename: nom du fichier versé (`Document.filename`) — sert uniquement à
            reconnaître un `.eml` (ADR-044 §5.4), que `document_format` seul ne
            distingue pas des autres `UNSUPPORTED` sans cette contrainte moteur.
            Défaut `""` : ne correspond jamais à `.eml`, comportement inchangé
            pour un appelant qui ne le passe pas.
        two_column_page_count: pages à deux sections côte à côte vues au precheck
            (ADR-042). Avec `table_count`, désigne le cas Docling d'ADR-043.
        table_count: tableaux vus au precheck. **Ne décide de rien seul** — c'est
            `decision.optimize_for_tables` qui porte l'intention de l'utilisateur ;
            ce compte ne sert qu'à reconnaître le cas colonnes+tableaux.
        page_count: nombre de pages du document (`Document.precheck_report`),
            qui dimensionne `task_page_size` sur le chemin Docling. Sans effet
            sur les deux autres profils.

    Returns:
        Le profil à passer à `IngestionEnginePort.start_parsing`.

    Le défaut de ces trois derniers arguments (`0`, `0`, `None`) ne route
    jamais vers Docling : un appelant qui ne les passe pas obtient exactement
    le comportement d'avant ADR-043.
    """
    if document_format is PrecheckFormat.IMAGE:
        # Les deux options sont sans effet sur une image, et le dire vaut mieux
        # que de les laisser produire un profil que le moteur rejetterait :
        # `picture` OCRise inconditionnellement (journal du moteur :
        # « Finish OCR » sur les deux variantes essayées), et poser
        # `layout_recognize` par-dessus n'a rien changé — ni au nombre de
        # fragments, ni à leur contenu, ni au score de recherche. Le profil est
        # donc vide des deux côtés : aucun template, aucun paramètre.
        return ChunkingProfile(chunk_method=NO_CHUNK_METHOD, parser_config={})

    if document_format is PrecheckFormat.PRESENTATION:
        # Même court-circuit, même garde moteur, même profil vide (§ docstring
        # de module et `NO_CHUNK_METHOD`). Le `parser_config` est vide lui aussi
        # parce qu'il ne servirait à rien : `rag/app/presentation.py::chunk()`
        # ne lit `layout_recognize` que sur la branche `.pdf`, jamais sur un
        # `.pptx` — et `use_parent_child` n'est lu que par `rag/app/naive.py`,
        # que ce format n'emprunte pas. Mesuré sur le document 31 du corpus :
        # 4 fragments, identiques avec ou sans `parser_config`.
        return ChunkingProfile(chunk_method=NO_CHUNK_METHOD, parser_config={})

    if filename.lower().endswith(".eml"):
        # Même garde moteur que l'image et le diaporama, mesurée directement
        # plutôt que supposée (ADR-044 §5.4) : un `.eml` classé `UNSUPPORTED`
        # (précheck ne l'analyse pas, comme `.docx`/`.md`) tombait par défaut
        # sur `chunk_method=naive`, que le moteur refuse verbatim
        # (« file type not supported yet(pdf, xlsx, doc, docx, txt supported) »).
        # Sans rien poser, le moteur choisit lui-même son propre template
        # `email` et rend 2 fragments propres (vérifié en direct : `run=done`,
        # `effective chunk_method=email`). `.eml` n'a pas de valeur dédiée dans
        # `PrecheckFormat` (contrairement à `PRESENTATION`) : c'est le nom de
        # fichier, pas le format précheck, qui distingue ce cas des autres
        # `UNSUPPORTED` qui n'ont pas cette contrainte.
        return ChunkingProfile(chunk_method=NO_CHUNK_METHOD, parser_config={})

    # Sélection fine (Phase 15) : elle **remplace** le template qu'aurait choisi
    # le routage automatique, et rien d'autre. Lue ici, une seule fois, plutôt
    # qu'à chaque branche — les deux branches restantes s'en servent comme d'un
    # défaut de remplacement, ce qui garde la logique de `layout_recognize`
    # strictement inchangée. Les deux formats court-circuités plus haut
    # l'ignorent délibérément (cf. `PrecheckDecision.chunk_method_override`) :
    # le moteur y refuse tout `chunk_method`, l'honorer ferait échouer le
    # document au lieu de l'indexer.
    override = decision.chunk_method_override

    optimize_for_tables_on_a_spreadsheet = (
        decision.optimize_for_tables and document_format is PrecheckFormat.SPREADSHEET
    )
    if optimize_for_tables_on_a_spreadsheet:
        # Chemin tableur inchangé (Étape 5, hors mandat) : seul `heavy_ocr` y
        # renforce la mise en page. `is_likely_scanned` n'y a jamais de sens
        # (un tableur n'a pas de notion de scan, `precheck/spreadsheet.py` le
        # fixe toujours à `False`) et le template `table` ne passe pas par
        # l'analyse de mise en page.
        parser_config: dict[str, JsonValue] = (
            {LAYOUT_RECOGNIZE_KEY: HEAVY_OCR_LAYOUT_RECOGNIZE} if decision.heavy_ocr else {}
        )
        parser_config.update(_additive_parser_config(decision))
        return ChunkingProfile(
            chunk_method=override or TABLE_CHUNK_METHOD, parser_config=parser_config
        )

    force_layout_analysis = decision.heavy_ocr or decision.optimize_for_tables
    # Trois branches là où il y en avait deux, dans cet ordre exact : la garde
    # d'ADR-023 (`is_likely_scanned`) passe **avant** le raffinement d'ADR-043,
    # donc un scan ne peut pas atterrir sur Docling — c'est la garde que le
    # docstring de `PLAIN_TEXT_LAYOUT_RECOGNIZE` déclare intouchable, et son
    # sens ne change pas ici.
    if is_likely_scanned:
        layout_recognize = HEAVY_OCR_LAYOUT_RECOGNIZE
    elif not force_layout_analysis:
        layout_recognize = PLAIN_TEXT_LAYOUT_RECOGNIZE
    elif _layout_analysis_is_served_by_docling(
        document_format=document_format,
        two_column_page_count=two_column_page_count,
        table_count=table_count,
    ):
        layout_recognize = DOCLING_LAYOUT_RECOGNIZE
    else:
        layout_recognize = HEAVY_OCR_LAYOUT_RECOGNIZE

    parser_config = {LAYOUT_RECOGNIZE_KEY: layout_recognize}
    if layout_recognize == DOCLING_LAYOUT_RECOGNIZE:
        parser_config[TASK_PAGE_SIZE_KEY] = _single_task_page_size(page_count)
    parser_config.update(_additive_parser_config(decision))
    return ChunkingProfile(
        chunk_method=override or DEFAULT_CHUNK_METHOD, parser_config=parser_config
    )
