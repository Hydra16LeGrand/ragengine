# ADR-035 — Prompt de description des figures : substituer une ressource du moteur, pas la décrire

**Date :** 2026-08-13 · **Statut :** accepté · **dénoue la question ouverte d'ADR-034**

## Résumé

- **ADR-034 avait mesuré le défaut et laissé la politique ouverte.** Elle est tranchée ici, sur
  décision utilisateur du 2026-08-12, en connaissance du coût de maintenance : le prompt de
  description des figures de RAGFlow est **remplacé dans l'image dérivée**, comme le modèle OCR
  l'a été par ADR-030.
- **Pourquoi ce n'est pas un fork déguisé** (l'argument qui avait fait écarter l'option dans
  ADR-034) : on substitue une **ressource** que le moteur compose lui-même — un fichier `.md`
  chargé par chemin, exactement comme `rec.onnx`/`ocr.res` — sans toucher une ligne de son code.
  C'est le même geste qu'ADR-030, et il est encadré par les mêmes garde-fous.
- **Le prompt ne branche sur aucun type d'image.** Il énonce à quoi sert la description (elle est
  indexée comme un fragment du document, retrouvée par recherche sémantique et lexicale) et en
  déduit les exigences. Une taxonomie se serait cassée sur tout ce qu'elle n'aurait pas énuméré.
- **Mesuré à modèle et image constants, sur six images de cinq natures** : sortie **45 à 91 % plus
  courte**, restitution des données **strictement égale** (18/18, 17/17, 7/7, 2/2), vocabulaire
  d'apparence et de mise en page **ramené à zéro**, et la langue qui passe de l'anglais au
  français partout où le document est français.
- **Les deux fichiers de prompt sont remplacés**, pas seulement celui du chemin par défaut : n'en
  traiter qu'un aurait fait revenir l'ancien comportement le jour où `image_context_size` serait
  posé.
- **Conséquence assumée** : deux divergences avec l'amont à revalider à chaque montée de version
  de RAGFlow. Le tag de l'image devient `v0.26.4-ragengine.1` et cesse de nommer une divergence
  particulière.

## Contexte — ce qu'ADR-034 avait établi, et ce qu'elle avait laissé ouvert

ADR-034 a montré, mesures à l'appui, que les descriptions d'images indexées décrivaient
l'**apparence** au lieu d'extraire le **contenu**, et le faisaient en **anglais** sur un document
français. Elle a isolé la cause — le prompt, pas le modèle — et vérifié qu'il n'est pilotable par
aucun réglage : `rag/prompts/template.py::load_prompt()` lit un fichier du répertoire du paquet,
sans variable d'environnement, sans configuration, sans surcharge par tenant. Elle a aussi écarté
sur mesure le seul levier voisin, `parser_config.image_context_size`, qui bascule vers la variante
« avec contexte » du **même** prompt.

Elle s'est arrêtée là volontairement : la décision engageait un arbitrage produit, pas une
correction technique. Six options y sont listées, dont « recouvrir le fichier du fournisseur »,
écartée alors au titre d'ADR-001.

Le fait qui a fait pencher la décision est dans la mesure elle-même : une figure jugée « sans
information exploitable » — une signature manuscrite — portait en réalité la mention **« Lu et
approuvé »**, à portée juridique dans un contrat. Le prompt d'origine la noyait dans quatre lignes
de description de tracé. Le coût du défaut n'est donc pas seulement un index dilué, c'est de
l'information perdue.

## Décision

**Le prompt de description des figures est remplacé, dans l'image dérivée, par un prompt écrit et
versionné par nous** (`docker/ragflow-prompts/`, copié par `docker/ragflow-cpu.Dockerfile`).

### Pourquoi ADR-001 n'y fait pas obstacle

ADR-034 écartait cette voie comme « un fork déguisé ». La distinction qui la rend acceptable est
celle qu'ADR-030 avait déjà tranchée dans l'autre sens : **on ne modifie aucun code du moteur, on
substitue une ressource qu'il compose lui-même**. `TextRecognizer` compose
`os.path.join(model_dir, "ocr.res")` ; `load_prompt()` compose
`os.path.join(PROMPT_DIR, f"{name}.md")`. Dans les deux cas le moteur va chercher un fichier à un
emplacement qu'il détermine seul, et on met un autre fichier à cet emplacement. Aucun contournement
de brique interne, aucune réimplémentation, aucun patch de source : le pipeline de RAGFlow s'exécute
exactement comme il est conçu.

**Vérifié, et c'est ce qui rend la substitution sûre : il n'y a aucun contrat de forme en aval.**
`figure_parser.py` récupère la sortie du modèle et la concatène telle quelle
(`descriptions[i] = txt + "\n".join(descriptions[i])`), sans parsing, sans marqueur extrait, sans
schéma attendu — le texte devient directement du contenu de fragment. Changer le prompt ne peut donc
casser aucune structure : il n'y en a pas. La seule contrainte de forme réelle est le
`@timeout(30, 3)` qui encadre l'appel, et elle joue en notre faveur puisque nos sorties sont plus
courtes.

### Ce que dit le prompt — et ce qu'il ne dit pas

Le prompt **ne contient aucune règle de décision par type d'image**. C'est délibéré, et c'est la
différence avec celui de l'amont, qui ouvrait sur une « DECISION RULE » distinguant les images
porteuses de « données énumérables » du reste. Une taxonomie à deux branches se casse sur tout ce
qu'elle n'énumère pas : photos, captures d'écran, logos, organigrammes, plans, cartes, formulaires
manuscrits, cachets.

À la place, le prompt pose le contexte réel — le système est un RAG d'entreprise généraliste et
multilingue, l'image arrive sans qu'on sache ce qu'elle représente, et **la description sera
indexée comme un fragment du document**, retrouvée par recherche sémantique et lexicale — puis
énonce les exigences qui en découlent :

- transcrire fidèlement le texte visible, c'est sur lui que tombent les requêtes ;
- restituer l'information portée : valeurs, relations, structure, ce que la figure établit ;
- dire ce qu'est la figure et à quoi elle sert, dans des mots qu'on chercherait ;
- **adapter la profondeur à l'information réellement présente** — un graphique mérite ses données,
  un logo mérite une ligne. C'est cette règle qui remplace la taxonomie, et elle vaut pour les
  natures d'images que personne n'a énumérées.

Trois interdits explicites : décrire l'apparence pour elle-même, commenter la mise en page, écrire
ce qui n'est pas visible. Et une règle de langue : celle du texte visible dans l'image, à défaut la
langue dominante du document — jamais une langue imposée (le système est multilingue par
conception, CONTRIBUTING.md §1 et ADR-003 ; imposer le français aurait corrigé un défaut en en créant un
autre).

### Les deux fichiers sont remplacés, pas un

`figure_parser.py` choisit `vision_llm_figure_describe_prompt()` quand aucun contexte n'entoure la
figure — notre chemin actuel, journal `prompt=default` — et
`vision_llm_figure_describe_prompt_with_context()` sinon. Les deux fichiers sont donc substitués,
avec la même intention, les mêmes interdits et la même règle de langue ; seul le second exploite en
plus le contexte reçu, et lui interdit toujours d'être une source d'information.

N'en remplacer qu'un aurait rendu le comportement dépendant d'un réglage que personne n'aurait relié
au prompt : poser un jour `image_context_size` aurait silencieusement fait revenir la description
d'apparence. C'est exactement le type de défaut différé que ce dépôt cherche à éviter.

**Périmètre réel** : `deepdoc/parser/mineru_parser.py` est un troisième appelant du même prompt.
RAGEngine n'utilise pas MinerU (`USE_DOCLING: "false"`, routage DeepDOC/Plain Text d'ADR-023) :
l'effet y est théorique, mais il existe, et il est mentionné plutôt que passé sous silence.

## Garde-fou de construction

`load_prompt()` échouerait par `FileNotFoundError` sur un nom ou un répertoire faux — au moment de
la première ingestion, pas au build. Pire : un fichier copié au mauvais endroit laisserait celui de
l'amont en place, et l'image sortirait du build **en paraissant corrigée**. Le `RUN` de garde
rejoue donc le chargement réel des deux prompts et vérifie que c'est bien le nôtre qui en sort
(marqueur présent, phrase caractéristique de l'amont absente), plus la préservation des deux
variables Jinja de la variante « avec contexte ». La comparaison porte sur le texte **aplati**
(espaces normalisés) : un marqueur cherché tel quel échouait au premier repli de ligne du fichier —
constaté au premier build, corrigé.

Sortie du build : `Prompt figures OK : 3181 et 3445 caracteres charges par load_prompt()`.

## Mesure — six images, cinq natures, deux prompts, modèle constant

Deux campagnes complémentaires, parce qu'aucune ne suffit seule.

**Pourquoi une mesure isolée en plus de l'ingestion réelle** : RAGFlow concatène à la description
vision les lignes que DeepDOC avait déjà associées à la figure (sa légende, l'OCR de sa zone). Sur
l'histogramme du corpus, cette queue OCR contient à elle seule les huit valeurs : le rappel de la
vérité terrain y est de 41/44 **même si la description vision était vide**. Mesurer le prompt sur le
fragment indexé reviendrait donc à mesurer surtout l'OCR de DeepDOC.

### A/B isolé — `compare_figure_prompts.py` (outil interne)

Le modèle est appelé directement, une image et un prompt à la fois, en reproduisant l'appel de
RAGFlow (`GeminiCV.describe_with_prompt`). Modèle **`gemini-3.6-flash`**, celui du déploiement
(ADR-027). Deux essais par image du corpus, un par image personnelle. Résultats bruts dans
`figure-prompt-ab-20260813-071712` (mesure interne).

| image | prompt | caractères | langue | faits restitués | apparence /100c | mise en page /100c |
|---|---|---|---|---|---|---|
| histogramme groupé | amont | 572 | fr | **18/18** | 0,20 | 0,00 |
| histogramme groupé | **ADR-035** | **314** | fr | **18/18** | **0,00** | 0,00 |
| tableau en image | amont | 485 | fr | **17/17** | 0,00 | 0,00 |
| tableau en image | **ADR-035** | **276** | fr | **17/17** | 0,00 | 0,00 |
| organigramme | amont | 918 | **en** | 7/7 | 1,10 | 0,80 |
| organigramme | **ADR-035** | **199** | **fr** | 7/7 | **0,00** | **0,00** |
| logo | amont | 443 | **en** | 2/2 | 2,40 | 1,00 |
| logo | **ADR-035** | **54** | fr | 2/2 | **0,00** | **0,00** |
| bloc de signature (doc. personnel) | amont | 1020 | **en** | — | 1,20 | 1,30 |
| bloc de signature (doc. personnel) | **ADR-035** | **384** | **fr** | — | **0,00** | **0,00** |

Soit **−45 %, −43 %, −78 %, −88 % et −62 %** de longueur, **à restitution de données strictement
égale**, avec le vocabulaire d'apparence et de mise en page ramené à zéro partout.

Le sixième couple d'images a buté sur le quota journalier du palier gratuit (20 requêtes par
modèle et par jour) et a été mesuré sur `gemini-flash-lite-latest`, à modèle constant lui aussi,
dans `figure-prompt-ab-signature-flash-lite` (mesure interne) :

| image | prompt | caractères | langue | apparence /100c | mise en page /100c |
|---|---|---|---|---|---|
| signature manuscrite seule (doc. personnel) | amont | 150 | **en** | 2,70 | 1,30 |
| signature manuscrite seule (doc. personnel) | **ADR-035** | **14** | **fr** | **0,00** | **0,00** |

**14 caractères** : la mention manuscrite, et rien d'autre. La mention « Lu et approuvé » est
présente des deux côtés — mais d'un côté elle est le fragment, de l'autre elle est une incise dans
une description de tracé.

### Ce que les verbatims montrent mieux que les chiffres

Le cas le plus démonstratif est l'organigramme, une nature qu'aucune règle du prompt n'anticipe.

Amont (979 caractères, anglais) :
> *« At the top left, the text "Organigramme de la direction industrielle" is displayed in bold
> text. Centered below this text, a rectangular box contains the text "Direction industrielle".
> From the bottom center of the "Direction industrielle" box, three black arrows extend downward
> toward a row of three horizontally aligned rectangular boxes. […] »*

ADR-035 (194 caractères, français) :
> *Organigramme de la direction industrielle*
> *Direction industrielle → Production*
> *Direction industrielle → Maintenance*
> *Direction industrielle → Qualité*
> *Production → Atelier A*
> *Production → Atelier B*

Même information structurelle, cinq fois moins de texte, et une forme sur laquelle une requête
« qui est rattaché à la production » a une chance de tomber.

Sur le logo, la règle d'adaptation de la profondeur joue exactement comme prévu : 443 caractères de
triangle, de casse et de graisse de police d'un côté ; `NORVENTIS` / `Ingénierie industrielle` de
l'autre.

### Bout en bout — `measure_figure_prompt.py` (outil interne)

Vraie ingestion, vraie base RAGFlow jetable, vrais appels vision, contre l'image réellement
déployée avant et après reconstruction. Modèle `gemini-flash-lite-latest` des deux côtés (les
quotas journaliers des deux autres modèles étaient épuisés — cf. « Ce qui reste imparfait »).
Résultats dans `figure-prompt-avant-20260813-073535/` et `figure-prompt-apres-20260813-073801/`.

| document | fragments de figure | caractères | langue | faits | apparence /100c | mise en page /100c |
|---|---|---|---|---|---|---|
| `30-pdf-raster-figures-fr.pdf` — avant | 3 | 1612 | en, fr | 41/44 | 0,06 | 0,00 |
| `30-pdf-raster-figures-fr.pdf` — **après** | 3 | **1201** | fr | 41/44 | **0,00** | 0,00 |
| contrat de travail 1 p. (doc. personnel) — avant | 2 | 1105 | **en** | — | 1,18 | 0,54 |
| contrat de travail 1 p. (doc. personnel) — **après** | 2 | **377** | **fr** | — | **0,00** | **0,00** |

Les quatre marqueurs suivis sur le contrat (mention manuscrite d'approbation, raison sociale du
cachet, coordonnées de contact, libellé imprimé) sont **présents avant et après** : le gain est de
concision et de langue, pas au prix d'un fait perdu.

Trois vérifications de non-régression de la chaîne, au passage :

- le **tableau rendu en image** ressort inchangé, en HTML, des deux côtés — il ne passe pas par la
  vision mais par la reconnaissance de tableaux de DeepDOC : le changement de prompt ne l'affecte
  pas, ce qui est le comportement attendu ;
- le nombre de fragments et leur découpage sont identiques avant et après ;
- `rag/app/picture.py` (images **autonomes**, chemin `chunk_method=picture`) utilise un **autre**
  fichier de prompt, `vision_llm_describe_prompt.md`, qui n'est pas touché : la démonstration
  d'ADR-027 sur `07-image-shapes-only.png` reste valide telle quelle.

### Corpus de mesure

`30-pdf-raster-figures-fr.pdf` (corpus interne), généré par
`gen_chunking_corpus_figures.py` (outil interne), 100 % fictif. Il n'existait pas : aucun document versionné
n'atteignait le parseur de figures (un schéma vectoriel n'est pas classé comme figure par DeepDOC,
et une image autonome passe par un autre prompt). Quatre natures d'images matricielles y sont
intégrées, aux densités d'information volontairement opposées, avec une vérité terrain écrite dans
le générateur. Aucun document personnel de l'utilisateur n'y figure ni n'a été versionné : les deux
images du contrat ont été lues depuis le stockage de l'instance, mesurées, et ne sortent des
rapports que sous forme d'indicateurs chiffrés et de marqueurs nommés.

## Conséquences

- **Deux divergences avec l'amont, à revalider à chaque montée de version de RAGFlow.** C'est le
  coût assumé de la décision. Il est outillé plutôt que confié à la mémoire :
  `docker/ragflow-prompts/upstream-v0.26.4/` conserve la copie exacte et non annotée des deux
  prompts d'origine, et l'en-tête du Dockerfile donne la commande de `diff` à rejouer.
- **Le tag de l'image change** : `ragengine/ragflow-cpu:v0.26.4-ragengine.1`. Il ne nomme plus une
  divergence — il y en a deux — mais la révision de leur ensemble. Répercuté dans
  `docker/docker-compose.ragflow.yml`, ADR-030, plan interne et
  le document interne de synthèse.
- **Les documents déjà ingérés gardent leurs anciennes descriptions.** Rien ne les réécrit : le
  prompt n'intervient qu'à l'indexation. Une base existante ne bénéficie du nouveau comportement
  qu'après réingestion de ses documents.
- **Réversibilité** : retirer la couche `COPY` du Dockerfile et reconstruire. Aucune donnée indexée
  n'est liée au prompt ; seuls les documents ingérés entre-temps porteraient l'autre style de
  description.
- ADR-034 est mise à jour : sa question ouverte est dénouée, l'option retenue étant celle-ci.

## Ce qui reste imparfait, dit plutôt que contourné

- **Le modèle de mesure n'est pas le même pour les deux campagnes.** L'A/B isolé — la comparaison
  quantitative qui porte la décision — est sur `gemini-3.6-flash`, le modèle de production. La
  campagne de bout en bout est sur `gemini-flash-lite-latest`, parce que le palier gratuit de
  Gemini plafonne à **20 requêtes par modèle et par jour** et que les deux autres modèles étaient
  épuisés. Chaque comparaison reste à modèle constant, donc valide ; mais les longueurs absolues
  d'une campagne ne se comparent pas à celles de l'autre.
- **Une modification temporaire du déploiement a été faite pour cela, et défaite** : le modèle
  `gemini-flash-lite-latest` a été enregistré sur l'instance `ragengine-vision` et
  `Tenant.img2txt_id` pointé dessus le temps des deux ingestions, puis remis à
  `gemini-3.6-flash@ragengine-vision@Gemini` (vérifié) et le modèle temporaire passé en `inactive`.
  RAGFlow n'expose pas de suppression de modèle d'instance : la ligne subsiste, désactivée.
- **L'exigence « dire ce qu'est la figure » n'est honorée qu'inégalement.** Sur l'organigramme et
  le logo, la sortie l'énonce ; sur l'histogramme, elle commence par le titre du graphique sans
  nommer sa nature. L'information n'est pas perdue (le titre la porte), mais la consigne existe
  dans le prompt et le modèle ne la suit pas systématiquement. Renforcer cette phrase demanderait
  une nouvelle campagne pour ne pas livrer un prompt non mesuré : c'est un candidat identifié, pas
  une correction faite en douce.
- **`langdetect` n'est pas fiable sous ~60 caractères** : il classe `NORVENTIS / Ingénierie
  industrielle` en allemand. Les rapports marquent ces cas d'une astérisque plutôt que de les taire
  ou de les corriger à la main ; le verbatim y fait foi.
