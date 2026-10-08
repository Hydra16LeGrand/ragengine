# ADR-034 — Description des figures : le prompt appartient à RAGFlow, la politique reste à trancher

**Date :** 2026-08-12 · **Statut :** constat établi et mesuré · **question ouverte dénouée le
2026-08-13 par ADR-035** (cf. § Dénouement en fin de document — le reste est conservé tel quel,
c'est l'état des connaissances au moment où la décision a été prise)

## Résumé

- Les descriptions d'images indexées décrivent l'**apparence** au lieu d'extraire le **contenu**,
  et le font en **anglais** sur un document français. Mesuré sur un document réel : sur deux
  figures, 950 et 405 caractères de « two side-by-side sections above a footer line », « looped
  line segments », « a vertical cross-like mark ».
- **Cause identifiée : le prompt, pas le modèle.** Même modèle, mêmes images, prompt orienté
  extraction ⇒ sortie en français, structurée, 55 % et 81 % plus courte, avec **strictement plus**
  de faits exploitables.
- **Le prompt n'est pas pilotable.** Fichier statique embarqué dans l'image RAGFlow, chargé depuis
  le répertoire du paquet, sans variable d'environnement, sans configuration, sans surcharge par
  tenant. Le corriger imposerait de recouvrir un fichier du fournisseur — écarté (ADR-001).
- **Le seul levier pilotable dans cette zone est inerte** : `image_context_size` bascule vers la
  variante « avec contexte » du même prompt — mesuré, aucune amélioration.
- **Un levier existant, non conçu pour ça, coupe tout** : l'analyse de mise en page. Sans DeepDOC,
  aucune figure n'est extraite, donc aucune description n'est produite.

## Constat

Un contrat de travail d'une page, numérisé, indexé avec `heavy_ocr` produit trois fragments : le
texte du contrat, puis **deux descriptions de figures** produites par le modèle de vision
(ADR-027). La première relève utilement le cachet de l'employeur et ses coordonnées, mais consacre
l'essentiel de ses 935 caractères à la mise en page. La seconde, 351 caractères, ne décrit qu'une
apparence — traits, boucles, ligne horizontale — et se termine par un résidu d'OCR.

Pour un moteur de recherche, « traits anguleux, boucles courbes » est du bruit : aucune requête
utile n'y tombera, et ces fragments diluent l'index.

## D'où vient exactement ce texte

Chaîne établie en lisant le conteneur `ragflow-cpu` déployé, puis confirmée par ses journaux.

1. `rag/app/naive.py::by_deepdoc()` appelle `vision_figure_parser_pdf_wrapper()` — **et lui seul**
   l'appelle sur le chemin PDF.
2. `deepdoc/parser/figure_parser.py` n'a qu'une condition d'activation : l'existence d'un modèle
   `IMAGE2TEXT` par défaut pour le tenant (`get_tenant_default_model_by_type`). **Aucune clé de
   `parser_config` ne l'active ni ne le désactive.**
3. Le prompt choisi est `vision_llm_figure_describe_prompt()` quand `image_context_size` vaut 0
   (le défaut), sa variante `…_with_context()` sinon.
4. `rag/prompts/template.py::load_prompt()` lit `<répertoire du paquet>/<nom>.md` et met le
   résultat en cache. **Ni variable d'environnement, ni entrée de configuration, ni surcharge en
   base.** Le texte vit dans `rag/prompts/vision_llm_figure_describe_prompt.md`, embarqué dans
   l'image Docker.

Journal de l'ingestion concernée, verbatim :
`[VisionFigureParser] figure=0 context_size=0 context_len=0 prompt=default` (deux fois).

Le prompt lui-même demande explicitement ce qu'on lui reproche : « *Describe visible regions and
components in a stable order* », « *Describe spatial grouping, containment, and alignment of
elements* ». Il ne dit rien de la langue attendue — d'où l'anglais sur un document français.

## Mesure : le prompt est bien la cause

Même modèle (celui du tenant), mêmes images, seul le prompt change. Contenu anonymisé ici.

| Figure | Prompt RAGFlow (actuel) | Prompt orienté extraction |
|---|---|---|
| Section de signature | **950 car., anglais.** Décrit la disposition (« *two horizontal sections … placed side by side, positioned above a horizontal line* », « *a thin gray horizontal line is centered* »). Relève le cachet et ses coordonnées, noyés dans la mise en page. | **423 car., français.** `Fonction : Section de signature` puis trois blocs : texte imprimé, cachet (raison sociale, boîte postale, e-mail, téléphone, site), annotations manuscrites (« Lu et approuvé », des deux côtés). |
| Signature seule | **405 car., anglais.** Uniquement de l'apparence : « *stylized cursive strokes, loops, and a prominent horizontal line* ». | **76 car., français.** `Fonction : mention manuscrite et signature` / `Texte transcrit : Lu et approuvé`. |

Deux enseignements, tous deux contre-intuitifs avant la mesure :

- La sortie orientée extraction est **plus courte et plus riche à la fois** (−55 % et −81 %). Ce
  n'est pas un arbitrage volume/qualité : le volume actuel est du remplissage.
- La figure jugée « sans aucune information exploitable » **en portait une** : la mention
  manuscrite « Lu et approuvé », qui a une portée juridique dans un contrat. Le prompt actuel la
  mentionne, mais enfouie dans quatre lignes de description de tracé ; le prompt d'extraction en
  fait le seul contenu. **Une politique qui écarterait les figures « sans texte » perdrait donc
  bel et bien quelque chose** — argument à peser, pas à supposer.

## Levier pilotable évalué, et écarté sur mesure

`parser_config.image_context_size > 0` fait passer RAGFlow à la variante « avec contexte » du
prompt, qui reçoit le texte environnant du document. C'est la seule clé que RAGEngine pourrait
poser dans cette zone (via `parser_config.ext`, comme `children_delimiter` — elle n'est pas au
schéma `ParserConfig`).

**Mesuré sur les deux mêmes figures, avec un contexte français réel : aucun effet utile.** 909 et
358 caractères, toujours en anglais, toujours orientés apparence. Le corps du prompt « avec
contexte » est identique à l'autre, et il interdit d'ailleurs explicitement d'utiliser le contexte
comme source d'information. Levier inerte pour ce problème : ne pas le poser, ce serait un
contournement au coût réel (un appel vision plus cher, un contexte à transporter) pour un gain nul.

## Décision

1. **Le prompt de RAGFlow n'est pas modifié.** Le seul moyen serait de recouvrir
   `rag/prompts/vision_llm_figure_describe_prompt.md` par un montage de volume : un fork déguisé,
   qui casserait en silence à la première mise à jour du moteur. ADR-001 s'y oppose, et ce dépôt a
   déjà tranché dans ce sens ailleurs.
2. **`image_context_size` n'est pas posé** — mesuré sans effet (§ ci-dessus).
3. **La politique de description des figures n'est pas tranchée ici.** Elle engage un arbitrage
   coût/rappel qui appartient au produit, pas à une correction technique.

## Question ouverte — les options réelles, avec ce que la mesure permet d'en dire

- **A. Statu quo.** Toutes les figures d'un document passé par l'analyse de mise en page sont
  décrites. Coût : un appel vision par figure (~4,5 s mesurés, ADR-027). Effet : rappel maximal,
  index dilué par des descriptions d'apparence en anglais.
- **B. Filtrer à l'indexation, côté RAGEngine.** Supprimer après coup les fragments purement
  descriptifs, via l'API de fragments de RAGFlow. *Ce que la mesure dit* : il n'existe **aucun
  marqueur fiable** — sur le document examiné, les trois fragments portent un `image_id`, y
  compris celui du texte du contrat, donc ce champ ne discrimine pas. Le filtre serait
  heuristique (langue, tournures), et la mesure montre qu'il supprimerait au passage la mention
  « Lu et approuvé ».
- **C. Rendre la description d'images activable par document au precheck.** *Ce que la mesure
  dit* : impossible proprement en l'état. L'activation ne dépend que de `Tenant.img2txt_id`,
  global au déploiement (ADR-027) ; la piloter par document reviendrait à basculer un réglage
  partagé pendant que d'autres ingestions tournent — une course, pas une option.
- **D. Utiliser le levier existant, qui coupe déjà tout.** Le parseur de figures n'est appelé que
  dans la branche DeepDOC. Décocher « Analyser la mise en page » sur un PDF à couche texte propre
  supprime donc **toute** description d'image de ce document. *Réserve* : levier grossier et
  couplé — on perd aussi la détection des tableaux et l'ordre des fragments (ADR-023, ADR-031),
  et il est inopérant sur un document numérisé, routé vers DeepDOC quoi qu'il arrive.
- **E. Changer de modèle de vision.** *Ce que la mesure dit* : peu prometteur. Le modèle actuel
  suit fidèlement le prompt qu'on lui donne — c'est démontré par le fait qu'un autre prompt change
  tout. Un autre modèle recevrait le même « *describe visible regions and components* ».
- **F. Faire corriger le prompt en amont** (contribution au projet RAGFlow, ou attente d'une
  version qui rend le prompt configurable). Aucun coût local, délai non maîtrisé. C'est la seule
  voie qui résout la cause plutôt que ses effets.

## Conséquences

- Tant qu'aucune option n'est retenue, le comportement reste celui de l'option A, documenté ici
  pour qu'il ne soit pas pris pour un défaut de configuration.
- Un agent futur tenté de « corriger le prompt » doit lire cette ADR d'abord : le fichier est
  atteignable dans le conteneur, et le recouvrir marcherait — jusqu'à la prochaine mise à jour.

## Dénouement (2026-08-13) — ADR-035

**Aucune des six options n'a été retenue telle quelle. C'est une septième, écartée à tort ici, qui
l'emporte** : substituer le fichier de prompt dans l'**image dérivée** que le dépôt construit déjà
(`docker/ragflow-cpu.Dockerfile`), sur décision utilisateur du 2026-08-12 prise en connaissance du
coût de maintenance.

Ce que cette ADR avait mal qualifié : « recouvrir un fichier du fournisseur » y était traité comme
un fork déguisé, donc contraire à ADR-001. La distinction qui manquait est celle qu'ADR-030 avait
déjà tranchée dans l'autre sens quelques jours plus tôt — **substituer une ressource que le moteur
compose lui-même n'est pas modifier son code**. `load_prompt()` compose son chemin exactement comme
`TextRecognizer` compose celui de `ocr.res` ; dans les deux cas le moteur s'exécute tel qu'il est
conçu. Et il n'y a **aucun contrat de forme en aval** : `figure_parser.py` concatène la sortie du
modèle sans la parser (vérifié), donc rien ne peut se casser structurellement.

Le point de cette ADR qui a le plus pesé dans la décision est celui de la fin de la section
« Mesure » : la figure jugée sans information exploitable en portait une, à portée juridique. Le
coût du défaut n'était pas la dilution de l'index, c'était la perte d'information.

Ce qui reste vrai et n'est pas à remesurer : la cause (le prompt, pas le modèle), l'absence de tout
levier de configuration, l'inertie d'`image_context_size`, et l'absence de marqueur fiable pour
filtrer les fragments après coup (option B). Ce qui est **caduc** : la décision 1 (« le prompt de
RAGFlow n'est pas modifié ») et la conclusion que la politique restait à trancher.

Une correction de fond au passage, qui vaut au-delà de ce cas : le prompt d'ADR-035 **ne reproduit
pas la structure à deux branches** du prompt d'origine (« données énumérables » vs le reste). Une
taxonomie se casse sur tout ce qu'elle n'énumère pas — photos, captures d'écran, logos,
organigrammes, cartes, cachets. Le prompt retenu énonce à quoi sert la description et laisse le
modèle adapter sa profondeur, ce qui a été vérifié sur cinq natures d'images plutôt que deux.
