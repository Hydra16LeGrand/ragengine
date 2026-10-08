# ADR-043 — Docling, troisième profil de mise en page : paquet local dans l'image dérivée

**Date :** 2026-08-24 · **Statut :** **accepté** (câblage joint le 2026-08-25) — routage, correctif
du défaut n°2 et non-régression sont produits et joints plus bas.

**Ce que « accepté » ne prétend pas** : le harnais de corpus n'est pas vert de bout en bout. Il ne
l'était pas avant ce câblage non plus — deux exécutions du **même** code y rendent des jeux
d'échecs différents (instantanés `DeepDOC`, sniffing CSV du moteur, rangs de retrieval). Ces
instabilités sont antérieures et étrangères à cette ADR ; la démonstration est au §Non-régression,
et elle ne repose pas sur une impression mais sur trois faits vérifiables.

## Résumé

- **ADR-042 avait laissé un cas ouvert et l'avait dit** : sur un document à la fois **à colonnes et
  à tableaux**, `DeepDOC` respecte les tableaux mais entrelace les colonnes, `Plain Text` respecte
  les colonnes mais perd tableaux **et** positions. Aucun des deux profils ne gagne, et l'écran
  d'aiguillage expose l'arbitrage au lieu de le trancher.
- **Docling règle les deux griefs ensemble, mesuré** : `column_switch_rate` **0,023** (borne de
  lecture correcte 0,014, borne entrelacée 0,167 ; `Plain Text` 0,021, `DeepDOC` 0,168), **15
  tableaux HTML sur 21**, et **100 % de fragments positionnés** — la seule configuration testée qui
  atteint ce chiffre en même temps qu'un ordre de lecture correct.
- **La variante retenue est le paquet local, pas le sidecar `docling-serve`.** Le sidecar est plus
  rapide (173,4 s contre 325,5 s) mais rend **0 tableau** et **0 % de fragments positionnés** : il
  reperd exactement ce pour quoi on vient.
- **Ce n'est pas une dépendance ajoutée au moteur.** `rag/app/naive.py` livre déjà
  `PARSERS["docling"]`, et l'`entrypoint.sh` de l'amont installe `docling==2.71.0` **au démarrage du
  conteneur** quand `USE_DOCLING=true`. On installe la même version au build. La divergence porte
  sur le *quand* et le *comment*, pas sur le *quoi*.
- **Coût réel, plus bas qu'annoncé** : `+1,48 Go` d'image (8,23 -> 9,71 Go), parce que `torch` est
  épinglé sur la roue **CPU**. Une installation sans précaution tirait la roue CUDA (~2,5 Go inertes,
  constaté) — CONTRIBUTING.md §2 réserve la VRAM au service d'inférence, `ragflow-cpu` n'a aucun GPU.
- **Trois défauts d'intégration RAGFlow étaient identifiés** ; les deux qui concernaient RAGEngine
  sont traités (2026-08-24/25). Le deuxième — un `progress = 1.0` émis avant l'écriture de l'index —
  a été **reproduit sur la stack réelle** : `run == DONE` à 99,9 s avec zéro fragment et une
  recherche à **0 résultat**, le premier fragment n'arrivant qu'à 153,4 s. Le sondage attend
  désormais `chunk_count > 0`, mais **seulement sur le profil Docling**.
- **Le routage est automatique et silencieux**, à l'intérieur de la case « Analyser la mise en
  page » que l'utilisateur coche déjà : ce n'est pas une option de plus, c'est une meilleure
  exécution d'un choix déjà fait, et il n'y a pas de contrepartie de latence à lui faire arbitrer
  (325 s contre les 361 s du `DeepDOC` remplacé).

## Contexte

### Le cas qu'ADR-042 a explicitement laissé ouvert

ADR-042 §Décision, point 3 : « Quand les deux signaux coexistent, rien ne change : seul le mode
complet reconnaît les tableaux, seul le mode direct respecte les colonnes, et la mesure ne les
départage pas. » Trois documents réels du corpus tombent dans ce cas (`real-notice-2042.pdf`,
`real-cerfa-2035-sd.pdf`, `real-acl-paper.pdf`), et sa section « À revisiter » notait que trancher
`tableCount === 0` serait « une décision produit, pas un résultat de mesure ».

Cette ADR ne tranche pas cet arbitrage : elle **le supprime** en apportant un troisième profil qui
ne demande plus de choisir entre les deux pertes.

### Les deux griefs, et ce qu'ils coûtent

| | ordre de lecture | tableaux | position des fragments |
|---|---|---|---|
| `DeepDOC` | **entrelacé** (0,168 pour une borne à 0,167) | reconnus | 1,00 |
| `Plain Text` | correct (0,021) | **perdus** | **0,00** |

La position perdue n'est pas cosmétique : elle retire la page d'origine d'une citation, tout
repérage dans le document source, et rend l'ordre du panneau « Voir les fragments » non pertinent
(ADR-042 §Conséquences, ADR-029 §1).

### La piste PDF -> Markdown, mesurée puis écartée

Avant celle-ci, une conversion **en amont de l'ingestion** (document converti en Markdown, puis
donné au moteur) a été mesurée sur trois convertisseurs (`docling` en bibliothèque, `pymupdf4llm`,
`opendataloader`) — `experiment_pdf_to_markdown.py` (outil interne), résultats dans
`pdf-to-markdown-*` (mesure interne). Elle est écartée pour deux raisons :

1. **elle n'apporte rien sur l'ordre de lecture** face à `Plain Text`, qui lit déjà le flux de
   contenu colonne par colonne (0,021 mesuré sur la notice) ;
2. **elle ne corrige pas la position** — un Markdown n'a plus de coordonnées de page, donc le
   fragment indexé serait positionné à 0,00 comme sous `Plain Text`. Le grief principal resterait
   entier.

S'y ajoute qu'elle violerait CONTRIBUTING.md §3 : convertir avant d'envoyer au moteur, c'est doubler sa
brique de mise en page au lieu de l'utiliser.

### Le sidecar `docling-serve`, mesuré puis écarté

`DoclingParser` accepte un serveur externe via `DOCLING_SERVER_URL`, et son chemin distant est un
**autre code** que le chemin local (`_parse_pdf_remote` contre la conversion en processus). Mesuré
sur la même notice, dataset jetable, même image : 173,4 s (contre 325,5 s en local), mais
`engine_html_table_count` **0** et `positioned_chunk_ratio` **0,00**. Le sidecar reconstruit les
sections à partir d'un Markdown renvoyé par le serveur : ni cellule de tableau, ni coordonnée. Il
rachète de la latence avec exactement les deux propriétés qui motivent la décision.

## Mesure — `real-notice-2042.pdf` (41 pages, colonnes **et** tableaux)

`measure_engine_layout_recognize.py` (outil interne), dataset RAGFlow jetable, aucune base de l'utilisateur
touchée. Résultats bruts dans `ragflow-docling-real-path-20260823` (mesure interne). Bornes de
`column_switch_rate` auto-étalonnées sur le PDF source (méthode d'ADR-042) : lecture correcte
**0,014**, lecture entrelacée **0,167**.

| chemin | latence | fragments | `column_switch_rate` | positionnés | tableaux HTML | rappel vs pypdf |
|---|---:|---:|---:|---:|---:|---:|
| `DeepDOC` (ADR-042) | 361 s | — | 0,168 | 1,00 | oui | — |
| `Plain Text` (ADR-042) | 31 s | — | 0,021 | **0,00** | **0** | — |
| **Docling, paquet local** | **325,5 s** | 149 | **0,023** | **1,00** | **15 / 21** | 0,902 |
| Docling, tâches de 12 p. | 377,7 s | 149 | 0,023 | 1,00 | 15 / 21 | 0,902 |
| Docling, sidecar (écarté) | 173,4 s | 154 | 0,000 | **0,00** | **0** | 0,919 |

Cohérence des colonnes des 15 tableaux rendus : **0,788** en moyenne.

**Point de méthode, à ne pas confondre** : `docling` appelé **en bibliothèque** convertit la même
notice en **58,4 s**. Les 325,5 s sont le chemin réel du moteur, qui fait davantage (rendu des
images de page par `pdfplumber`, transfert section par section avec balises de position, découpage,
embedding, écriture ES). C'est le second chiffre qui engage la production, pas le premier.

## Décision

**Docling devient un troisième profil `layout_recognize`, servi par le paquet Python installé dans
l'image dérivée** (`docker/ragflow-cpu.Dockerfile`, troisième divergence après ADR-030 et ADR-035 ;
tag `ragengine/ragflow-cpu:v0.26.4-ragengine.2`).

### Ce que l'image embarque, et pourquoi au build

- `docling==2.71.0` — **exactement la version que l'amont installerait au démarrage**
  (`entrypoint.sh::ensure_docling`, défaut `DOCLING_VERSION==2.71.0`). Aucun code du moteur n'est
  touché : `PARSERS["docling"] -> by_docling` existe déjà.
- **Au build et pas au démarrage**, pour la raison d'ADR-030 : le mécanisme amont télécharge ~1,5 Go
  de roues depuis un miroir chinois à chaque création de conteneur, sans somme de contrôle, au pire
  moment. `USE_DOCLING` reste donc `"false"` dans le compose — c'est un drapeau d'*installation*,
  pas de fonctionnalité, et `check_installation()` ne le regarde pas.
- **`--torch-backend cpu` explicitement.** Sans lui, `uv` résout `torch` sur la roue CUDA (constaté
  le 2026-08-23). Le garde-fou de construction refuse une image où `torch.version.cuda` ne serait
  pas `None`, où `torch.cuda.is_available()` serait vrai, ou où un paquet `nvidia-*` se serait
  glissé. Vérifié sur l'image déployée : `torch 2.13.0+cpu`, `version.cuda None`,
  `cuda.is_available False`, `DeviceRequests` vide.
- **Les modèles sont figés dans l'image** (~565 Mo : mise en page « heron », TableFormer, RapidOCR)
  via `docling-tools models download`, et `DOCLING_ARTIFACTS_PATH` est posé en `ENV`. Sans cette
  variable, Docling ignore le cache local et repart sur le Hub — vérifié, la conversion échoue en
  `LocalEntryNotFoundError` hors ligne. Sans le préchargement, ce serait plusieurs centaines de Mo
  téléchargés **dans le chemin critique du premier job**.
- **`DOCLING_SERVER_URL` est déclaré vide** dans le compose. `_effective_server_url()` donne la
  priorité au serveur sur le paquet local : renseigner cette variable basculerait silencieusement
  sur la variante écartée.

### Ce que l'installation déplace dans l'image, dit plutôt que découvert plus tard

`docling==2.71.0` plafonne `pillow<12` et `huggingface_hub<1`. Trois paquets déjà présents changent
donc de version — `pillow` 12.2.0 -> 11.3.0, `huggingface_hub` 1.3.1 -> 0.36.2, `pydantic-settings`
2.12.0 -> 2.15.0 — et **aucun n'est supprimé**. RAGFlow n'utilise de `huggingface_hub` que
`snapshot_download` et `hf_hub_download`, stables des deux côtés de la majeure (vérifié : quatre
appelants, tous dans `deepdoc/`).

**Ces trois changements atterrissent après les garde-fous d'ADR-030 et d'ADR-035**, qui ont donc
validé un état de l'image qui n'est plus celui qui est livré. Le garde-fou de la nouvelle couche
rejoue leurs deux invariants — alignement CTC du dictionnaire OCR, présence du prompt de figures —
pour cette raison précise. Sortie du build :

```
Docling OK : 2.13.0+cpu - conversion hors ligne 1 sections, positions emises
Non-regression OK : ADR-030 504 classes - ADR-035 en place - pillow 11.3.0 - huggingface_hub 0.36.2
```

## Les trois défauts d'intégration RAGFlow, et ce que le câblage en a fait

Établis pendant la campagne de mesure, puis traités (2026-08-24).

1. **Les bornes de pages ne sont pas transmises.** `by_docling(filename, ..., from_page, to_page,
   ...)` reçoit les bornes et **ne les passe pas** à `parse_pdf()`, qui n'a pas de paramètre de
   plage. RAGFlow découpe pourtant tout PDF en tâches de 12 pages
   (`api/db/services/task_service.py`) : chaque tâche reconvertit et ré-embedde le **document
   entier**. Mesuré : un PDF de 121 pages converti 10 fois, à ~142 s la conversion. Latence en
   O(pages²). **Contournement mesuré** : forcer `parser_config.task_page_size` au-delà du nombre de
   pages du document, pour n'avoir qu'une seule tâche (377,7 s -> 325,5 s sur 41 pages ; l'écart
   explose avec la longueur).
   **Traité** : `chunking_profile.py` pose `task_page_size = page_count + 1` en même temps que
   `Docling`, jamais ailleurs — le nombre de pages vient déjà du precheck. Vérifié sur la stack
   réelle : `parser_config.task_page_size` relu du moteur vaut `5` sur le cerfa (4 pages) et `41`
   sur la notice (40 pages). Un `page_count` inconnu retombe sur le plafond du moteur
   (`MAXIMUM_PAGE_NUMBER`), jamais sur une tâche par page.
2. **`progress = 1.0` est émis avant l'écriture de l'index.** `DoclingParser.parse_pdf()` termine
   par `callback(1.0, "[Docling] Done.")` — à la fin du *parsing*, donc avant le découpage,
   l'embedding et l'écriture ES. **C'est le point le plus sensible pour RAGEngine** : un job qui ne
   sonderait que l'état RAGFlow peut passer `completed` sur un index vide.
   **Reproduit sur la stack réelle** (`real-notice-2042.pdf`, 40 pages, 2026-08-24) : le moteur
   annonce `run == DONE` à **99,9 s**, avec `chunk_count = 0` — et une recherche lancée *à cet
   instant précis* rend **0 résultat**. Le premier fragment n'apparaît qu'à **153,4 s**, soit une
   fenêtre de **53,5 s** (32,4 s sur une seconde ingestion) pendant laquelle l'ancien code aurait
   marqué le job `completed` sur un index vide.
   **Traité** : le critère de fin du sondage devient `chunk_count > 0` **quand, et seulement
   quand**, le profil posé est `Docling` (`workers/tasks/ingestion.py::
   _engine_finish_is_trustworthy`). Le signal est choisi pour son ordonnancement, pas pour sa
   commodité : `DocumentService.increment_chunk_num` suit `insert_chunks` dans la même fonction du
   moteur, et ne transite pas par le rapprochement périodique de progression — il continue donc
   d'être mis à jour après le passage à `DONE`. Il est en outre **conservateur** : lors de la
   seconde ingestion, la recherche rendait déjà 5 résultats alors que `chunk_count` valait encore 0
   — ce signal peut retarder la conclusion, jamais l'anticiper. Le job conclut désormais à 160,5 s,
   et une recherche immédiate rend 5 résultats sur 5.
   Pourquoi `DeepDOC` et `Plain Text` n'ont pas besoin de cette garde, vérifié dans la source
   déployée : leur dernier `progress_callback(prog=1.0)` est émis **après** l'écriture ES et
   `increment_chunk_num`, et `DocumentService._sync_progress` ne pose `run = DONE` que lorsque
   toutes les tâches sont à 1,0. Leur `done` arrive donc après l'index, pas avant.
3. **Le chunking natif du chemin distant est mort.** Sans objet pour la variante retenue : ce code
   n'appartient qu'à `_parse_pdf_remote`, et le sidecar est écarté. Mentionné pour que personne ne
   le rouvre en croyant à un gain disponible.

## Portée de routage — tranchée

**Docling est routé sur les documents où le precheck détecte à la fois des colonnes et des
tableaux** (`two_column_page_count > 0` **et** `table_count > 0`), sur un PDF non numérisé — exactement
le cas qu'ADR-042 laissait à l'arbitrage humain. Ce n'est **pas** un remplacement général :

- sur un document **sans tableau**, `Plain Text` fait aussi bien sur l'ordre (0,021 contre 0,023)
  pour 31 s contre 325 s — ADR-042 s'applique telle quelle ;
- sur un document **numérisé**, `is_likely_scanned` impose `DeepDOC` (ADR-023 §3) et rien ne change
  ici. Voir aussi la limite OCR ci-dessous ;
- sur un document **mono-colonne**, il n'y a aucun grief d'ordre de lecture à corriger.

### Automatique et silencieux, pas une quatrième case

Le routage **remplace `DeepDOC` à l'intérieur de la branche « analyse de mise en page »**, celle que
l'utilisateur ouvre en cochant « Analyser la mise en page » (proposée par défaut sur tout PDF depuis
ADR-031). Il n'apparaît pas au catalogue de `PrecheckDecision`. Trois raisons, dans cet ordre :

1. **Ce n'est pas un choix de plus, c'est une meilleure exécution d'un choix déjà fait.** ADR-031
   fait arbitrer à l'utilisateur des *intentions* (« analyse complète » contre « lecture directe »),
   jamais des noms de moteurs. Lui demander « DeepDOC ou Docling ? » lui poserait une question dont
   il n'a pas les éléments — et dont la mesure a déjà la réponse sur ce cas précis.
2. **Il n'y a pas d'arbitrage restant à exposer.** 325 s contre 361 s face au `DeepDOC` qu'il
   remplace : aucune régression de latence, et un gain sur les trois griefs à la fois. Le seul
   arbitrage que l'écran affichait — ordre de lecture *ou* tableaux — est précisément celui que
   cette ADR supprime.
3. **Décocher reste possible et reste respecté.** Sur le même document, case décochée, le profil
   reste `Plain Text` (31 s, colonnes respectées, tableaux perdus) : ADR-042 s'applique sans
   changement.

**Ce qui change à l'écran** : le seul texte concerné, `precheck.decision.ocrColumnsWithTables`,
exposait l'arbitrage (« à vous de choisir ce qui compte le plus »). Il devient faux le jour où le
routage tranche : il est réécrit pour dire ce qu'il advient réellement du document (les deux gains
tenus, la position conservée, la latence inchangée). Aucune logique d'interface n'est touchée —
`suggestedDecision` et `readsBetterWithoutLayoutAnalysis` restent identiques, le raffinement vit
entièrement côté backend, à partir des deux mêmes compteurs déjà affichés.

## Conséquences

**Assumées :**

- **Latence multipliée par ~10 face à `Plain Text`** sur les documents concernés (325 s contre 31 s
  sur 41 pages), et comparable à `DeepDOC` (361 s). Le profil ne se justifie que là où les deux
  griefs coexistent réellement.
- **Une divergence d'image de plus à revalider à chaque montée de version de RAGFlow.** Outillée
  plutôt que confiée à la mémoire : l'en-tête du Dockerfile donne la commande qui compare notre
  `ARG DOCLING_VERSION` au défaut de `ensure_docling()` de la nouvelle image.
- **`+1,48 Go` d'image** (8,23 -> 9,71 Go), dont ~565 Mo de modèles figés. Sans le préchargement,
  l'image serait plus petite et la première ingestion plus fragile : compromis assumé dans le sens
  du déterminisme, comme ADR-030.
- **6 tableaux sur 21 ne ressortent pas en HTML**, et la cohérence de colonnes des 15 rendus est de
  0,788. C'est mieux que `Plain Text` (0), pas parfait.
- **L'OCR de Docling porte le défaut qu'ADR-030 a corrigé pour DeepDOC.** `PdfPipelineOptions()`
  active `do_ocr` par défaut, et le moteur retenu par `auto` est RapidOCR, chargé — vérifié dans les
  journaux du conteneur — sur `ch_PP-OCRv4_rec_infer.onnx`, un modèle **chinois**. Portée réelle
  faible : l'OCR ne se déclenche que sur des zones sans couche texte, et le routage proposé vise des
  documents nativement numériques ; un scan reste routé vers `DeepDOC` par `is_likely_scanned`. À
  remesurer avant tout élargissement du routage aux documents numérisés.

**Favorables, chiffrées :**

- **Les positions sont conservées à 100 %** — citations, page d'origine, ordre du panneau de
  consultation. C'est le seul profil qui les garde *et* lit dans le bon ordre.
- **Le rappel face à `pypdf` est de 0,902**, sans la perte de couverture qu'ADR-042 relevait sous
  `DeepDOC` sur ce document (272 349 caractères contre 291 740).
- **Aucune exposition supplémentaire au quota de l'API de vision** : `by_docling` ne passe pas par
  le parseur de figures de DeepDOC, donc n'émet aucun appel de description d'image (cause des quatre
  échecs d'ADR-042 §Conséquences).

## Vérification de l'image livrée

Image `ragengine/ragflow-cpu:v0.26.4-ragengine.2` construite, déployée sur `ragflow-cpu`, saine.

- `check_installation()` -> `True`, serveur effectif `''` : c'est bien le **paquet local**.
- `torch 2.13.0+cpu`, `torch.version.cuda is None`, `torch.cuda.is_available() False`, aucun paquet
  `nvidia-*`, `DeviceRequests` vide sur le conteneur.
- Ingestion de fumée bout en bout (`03-pdf-ruled-table.pdf`, dataset jetable supprimé ensuite) :
  `done`, 2 fragments, **positions 1,00**, **1 tableau HTML sur 1**, rappel 1,00, 20,4 s. Les
  journaux montrent les modèles chargés depuis `/root/.cache/docling/models` — **aucun
  téléchargement** à la première conversion.

## Vérification du câblage RAGEngine (2026-08-24/25)

### Ce qui a été mesuré sur la stack réelle

Deux ingestions complètes de `real-notice-2042.pdf` par la **façade** (upload, precheck, décision
`heavy_ocr`, sondage, recherche), base éphémère supprimée ensuite :

| | ingestion 1 | ingestion 2 |
|---|---:|---:|
| `parser_config` relu du moteur | `Docling` + `task_page_size: 41` | idem |
| moteur `run == DONE` | 99,9 s | 116,3 s |
| `chunk_count` à cet instant | **0** | **0** |
| recherche à cet instant | **0 résultat** | 5 résultats |
| premier fragment (`chunk_count > 0`) | 153,4 s | 148,7 s |
| **fenêtre du défaut n°2** | **53,5 s** | **32,4 s** |
| job `completed` | 160,5 s | 154,5 s |
| recherche juste après `completed` | 5 / 5 | 5 / 5 |
| fragments | **153** | **149** |
| positions | oui | oui |

Trois lectures de ce tableau :

1. **Le défaut n°2 est réel et se reproduit** : à l'ancien signal, l'index était vide et la
   recherche muette (ingestion 1). C'est ce job-là qui serait passé `completed`.
2. **Le nouveau signal est conservateur, jamais optimiste** : à l'ingestion 2, la recherche rendait
   déjà 5 résultats alors que `chunk_count` valait encore 0. `chunk_count` peut retarder la
   conclusion d'un job, il ne peut pas l'anticiper — exactement la propriété voulue.
3. **La sortie de Docling n'est pas reproductible** (153 puis 149 fragments, fichier identique).
   Le harnais de corpus le déclare : `Docling` rejoint `DeepDOC` dans `NON_REPRODUCIBLE_LAYOUTS`
   (`snapshot.py` (suite de mesure interne)), donc régime de figeage **tolérant**, jamais exact.

### Non-régression

- **Suite backend complète** : 1187 -> **1209 tests verts** (+22 : routage Docling, garde de fin de
  job, câblage du rapport de precheck). Le correctif de fin de job est verrouillé **rouge avant
  vert** — les quatre tests Docking échouent sur le code d'avant, les trois tests de non-régression
  `DeepDOC`/`Plain Text` passent des deux côtés.
- **Harnais de corpus, deux exécutions complètes** (`run-corpus-suite.sh` (outil interne), stack réelle) :
  5 puis 10 échecs sur 485 tests, **jeux d'échecs différents à code identique**. Aucun n'est
  imputable à ce câblage, et les trois raisons sont structurelles, pas des impressions :
  1. **aucun diff de référence ne porte sur `routing`** — le routage est figé dans chaque instantané
     et n'apparaît dans aucun écart : aucun document n'a changé de profil ;
  2. **le mot `Docling` n'apparaît nulle part dans les deux journaux** — aucun scénario exécuté par
     la suite n'atteint le nouveau chemin (les quatre documents concernés ne sont pas dans les
     sous-ensembles `heavy_ocr` joués) ;
  3. **les familles qui échouent sont celles déjà déclarées non déterministes** — instantanés
     `DeepDOC` (56, 59, 17), bug de sniffing CSV de RAGFlow (53, CSV USGS), rangs de retrieval
     (q06, q11).
  Un jeu d'échecs qui change d'une exécution à l'autre **à code identique** est d'ailleurs une
  meilleure preuve qu'une exécution de comparaison sur le code d'avant : celle-ci pourrait être
  verte par chance et incriminer à tort.
- **Découverte au passage, à instruire séparément** : `real-gdpr-fr.pdf` (42) présente la même
  bimodalité de découpage que 49 (248 puis 218 fragments, fichier identique) — mais, contrairement
  à 49, elle **n'est pas déclarée au manifeste**. Ce n'est pas une régression de ce câblage (42 n'a
  ni colonnes ni tableaux détectés, il ne peut pas atteindre Docling), c'est une exclusion
  manquante.
- **Frontend** : `npm run test` (52), `npm run lint`, `tsc --noEmit` verts. Un seul texte change,
  aucune logique.

### Le nouveau cas de test

`test_pdf_tables.py` (suite de mesure interne)
ingère le 49 sous `heavy_ocr` dans une base éphémère dédiée et verrouille les quatre promesses
ensemble : le routage relu du moteur, la position des fragments, les bornes d'extraction déclarées
au manifeste (`extraction_heavy_ocr:` du 49, plafond d'entrelacement posé au niveau de celui du
mode direct), et — c'est le point d'ADR-043 — qu'une recherche lancée **juste après** `completed`
rende des résultats. Pas de référence figée : la sortie n'est pas reproductible.

Mesuré à sa première exécution (2026-08-25, 4 min) : **149 fragments, entrelacement 0,335** pour un
plafond déclaré à 0,45, **couverture 0,744** (plancher 0,60), **mots reconnus 0,518** (plancher
0,35), **positions présentes**, **5 résultats sur 5** à la recherche immédiate.

## À revisiter

- Si l'amont transmet un jour `from_page`/`to_page` à `parse_pdf()`, le contournement
  `task_page_size` (défaut n°1) devient inutile et doit être retiré, pas laissé en place.
- Si l'amont change son défaut `DOCLING_VERSION`, rejouer la mesure avant de suivre : c'est une
  version de moteur de mise en page, pas un numéro de patch anodin.
- **La fenêtre d'attente du défaut n°2 n'est bornée que par la veille anti-blocage**
  (`ragflow_stuck_job_timeout_seconds`, 900 s). Mesurée à 53,5 s sur 40 pages, elle croît avec le
  nombre de fragments à vectoriser : sur un document de plusieurs centaines de pages routé vers
  Docling (le 48 du corpus en fait 228), il faudra vérifier qu'elle reste sous ce plafond, ou le
  relever. Un dépassement ferait échouer le job — jamais le compléter à tort.
- **La description OpenAPI de `heavy_ocr`** (`domain/schemas/document.py`) dit encore que l'option
  impose `DeepDOC`. Elle est désormais incomplète pour le cas colonnes+tableaux. La corriger exige
  de régénérer `frontend/src/api/schema.ts` contre le serveur vivant
  (`scripts/check-api-drift.sh --fix`), donc un redéploiement de la façade : à faire au prochain
  redémarrage, pas au milieu d'une ingestion en cours.
- **`render_corpus_readme.py` (outil interne) est cassé** (`KeyError: 'id'` sur une entrée
  `accepted_failures` sans identifiant) — panne antérieure à ce câblage, vérifiée sur le manifeste
  d'avant. le README du corpus interne n'a donc pas pu être régénéré.
