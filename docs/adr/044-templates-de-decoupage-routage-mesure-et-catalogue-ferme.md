# ADR-044 — Templates de découpage : routage mesuré, catalogue fermé, et l'index qui empêche l'oubli

**Date :** 2026-08-29 · **Statut :** **accepté** — le travail est livré et vérifié (commits
`3ea5793`, `c370a97`), ce n'est plus une proposition. Clôt la Phase 15.

## Résumé

- **Le sujet de départ** : RAGFlow expose 13 templates de découpage et 8 moteurs de mise en page ;
  RAGEngine en utilisait 2 (`naive`, `table`) et 3 (DeepDOC, Plain Text, Docling). ADR-023, ADR-042
  et ADR-043 avaient traité l'axe *moteur*, jamais l'axe *template*.
- **Le vrai sujet** : la cartographie `chunk_method × document` **existait déjà** (Phase 11,
  2026-08-07, `explore-chunking-matrix-20260807-113300/MATRIX-SUMMARY.md`). Elle n'était référencée
  que dans le plan interne de la phase 11 — l'archive de sa propre phase. **Archiver a suffi
  à faire disparaître sa trouvaille du champ de vision pendant trois semaines.** Le correctif
  principal de cette ADR n'est pas un template, c'est le §4.
- **Docling reste cantonné à colonnes+tableaux** (ADR-043), mesuré : sur un scan réel il rend
  **33,3 % de fragments dégénérés** contre 0 pour DeepDOC ; sur `.docx` il est à égalité (rappel
  croisé 0,9875) ; sur `.pptx` et sur une image il n'est **jamais atteint**, le moteur imposant son
  propre template.
- **Un bug de production trouvé et corrigé** : tout diaporama échouait au découpage parce que
  RAGEngine posait `naive` dessus, ce que le moteur refuse sur `.ppt`/`.pptx`/`.pages` **quelle que
  soit la valeur**. `failed`/0 fragment avant, `completed`/4 fragments après (`3ea5793`).
- **Un catalogue fermé de cinq templates** (`naive`, `laws`, `paper`, `manual`, `qa`) est ouvert en
  « Paramètres avancés », chaque entrée adossée à un chiffre et chaque exclusion à un défaut mesuré.
  Le filtrage *par document* vit à l'écran, le catalogue *permis* vit au schéma — la frontière est
  volontaire (§2).
- **`email` est écarté sur défaut mesuré, pas par prudence** : posé sur un PDF il rend `run=done` en
  indexant les octets bruts du fichier (couverture source **2,2 %**). Un succès apparent qui indexe
  du binaire est pire qu'un échec.
- **Quatre dettes sont consignées, aucune n'est corrigée ici** (§5) : le flake d'ingestion des
  tableurs, les notes de présentateur `.pptx` jamais lues, `render_corpus_readme.py` cassé, et le
  `.eml` déclaré « rejet moteur » alors que la mesure dit le contraire.

## Contexte

### Ce qui était su, et ce qui ne l'était pas

Deux templates sur treize étaient utilisés, et ce n'était pas une décision : personne n'avait
mesuré les onze autres depuis la Phase 11. Cette phase a repris cette cartographie plutôt que de la
refaire, l'a croisée avec l'axe moteur qu'elle laissait fixe, et a produit deux rapports :

- `docling-coverage-20260826` (mesure interne) — 8 cas hors colonnes+tableaux ;
- `chunk-method-by-family-20260826` (mesure interne) — 12 cas, template × famille, le
  moteur de mise en page tenu constant pour que la seule variable qui bouge soit le template.

Les deux s'appuient sur la sonde factorisée `ragflow_probe.py` (outil interne) (Étape 0, `787772d`), qui a
remplacé **7** réimplémentations indépendantes du même geste — dataset jetable, upload,
`parser_config` libre, attente de l'état terminal, pagination des fragments, nettoyage garanti.

### Le mécanisme de perte, qui est le vrai sujet

`MATRIX-SUMMARY.md` **était** référencé. La convention de rangement a été respectée. Ce qui a
manqué, c'est que la référence vivait dans un fichier que l'archivage de la phase a rendu inutile à
ouvrir. Une règle de plus n'aurait rien changé : il y en avait déjà une, et elle a été suivie.

Le constat, chiffré au 2026-08-29 sur les 31 dossiers des résultats de mesure internes :

| situation (catégories exclusives) | dossiers |
|---|---:|
| cités par leur nom depuis une ADR, le plan courant ou le code | 13 |
| cités seulement par un motif générique (`column-reading-order-*`, `pdf-to-markdown-*`) | 10 |
| cités **seulement depuis l'archive de leur propre phase** | 3 |
| cités **nulle part** | 5 |
| **total** | **31** |

Les 3 de l'avant-dernière ligne sont exactement le cas `MATRIX-SUMMARY.md` : perdus par
construction, pas par négligence.

## Décision

### 1. Routage automatique — les paires retenues

Aucune paire nouvelle n'est adoptée « par défaut générique ». Le seul changement est le diaporama,
et c'est un correctif :

| famille | `layout_recognize` | `chunk_method` posé par RAGEngine | preuve |
|---|---|---|---|
| PDF à texte, sans colonnes | `Plain Text` (`DeepDOC` si « analyser la mise en page ») | `naive` | ADR-029, ADR-031 |
| PDF à colonnes **et** tableaux | `Docling` | `naive` | ADR-043 |
| PDF numérisé | `DeepDOC` | `naive` | ADR-023 |
| Tableur, case « optimiser pour les tableaux » | — | `table` | 178 fragments contre 51 sur le CSV USGS ; limite §5.5 |
| **Diaporama** (`.ppt`/`.pptx`/`.pages`) | — | **aucun** (profil vide) | `failed`/0 → `completed`/4 fragments |
| Image | — | aucun (profil vide) | le moteur impose `picture` |

**Le correctif du diaporama.** `validate_chunk_method` (`api/utils/validation_utils.py` de l'image
déployée) refuse la requête entière dès que la clé `chunk_method` est **présente** sur un
`.ppt`/`.pptx`/`.pages` — y compris avec la valeur `presentation` que le moteur s'assigne pourtant
lui-même au téléversement. RAGEngine posait `naive` : tout diaporama échouait avant d'avoir produit
un fragment. Mesuré sur le document 31 : bras `production` refusé (`Not supported yet!`), bras
`sans-rien-poser` → 4 fragments, 195 caractères médians, mots reconnus 0,962. `PrecheckFormat.PRESENTATION`
reconnaît désormais exactement les trois extensions de la garde du moteur, et `profile_for_decision`
rend un profil vide — même court-circuit que pour une image.

**Ce qui ne change pas, et pourquoi.** `laws`/`paper`/`manual` segmentent nettement plus finement
que `naive` (RGPD 88 p : 545 fragments de 429 caractères médians contre 248 de 1773, couverture
source 0,888 contre 0,892, rappel croisé 1,0 dans les deux sens), mais **aucune métrique de
pertinence de recherche n'a été mesurée**. Une segmentation plus fine n'est pas un meilleur
retrieval : c'est une hypothèse, et elle reste non instruite. Basculer le défaut sur cette base
serait exactement l'adoption sur intuition que la DoD de cette phase interdit.

**Le gain vient du template, pas du moteur** — le seul croisement des deux axes tranche la question
laissée ouverte par la Phase 11 : sur le document 11, `laws` rend 9 fragments sous `Plain Text`
comme sous `DeepDOC`, `paper` idem. Le moteur de mise en page n'y est pour rien.

### 2. Option avancée — catalogue fermé, filtrage par document à l'écran

`PrecheckDecision.chunk_method_override` (`backend/app/domain/schemas/document.py`) accepte cinq
valeurs, pas treize (`AdvancedChunkMethod`). Elle **remplace** le template du routage automatique et
rien d'autre : `layout_recognize` — Docling compris — et les clés additives sont calculés comme si
elle était absente, si bien que la sélection fine et « Analyser la mise en page » restent
cumulables. Défaut `null` ⇒ contrat v1 inchangé.

Le filtrage par document vit dans `frontend/src/api/precheck.ts::advancedChunkMethodsFor` — le
catalogue dit ce qui est *permis*, l'écran dit ce qui est *pertinent* :

| proposé | condition exacte (`advancedChunkMethodsFor`) | mesure |
|---|---|---|
| `laws`, `paper`, `manual` | `format === 'pdf'` **et** `supported` **et** `!isLikelyScanned` **et** `twoColumnPageCount === 0` | gagnants sur PDF structuré (RGPD, ACL) ; `laws` s'effondre à **19,2 % de fragments dégénérés** sur l'article ACL à deux colonnes, contre 0 % pour `naive`/`paper`/`manual` |
| `qa` | `format === 'unsupported'` **et** extension ∈ `.md`/`.markdown`/`.txt` | Markdown : 4 fragments `Question: … Answer: …` exploitables ; PDF de FAQ pourtant rédigé « Q : / R : » : échec moteur « Unable to recognize Q&A structure. », 0 fragment |
| rien | tout le reste (scan, image, diaporama, tableur, fichier abîmé) | le panneau n'apparaît pas plutôt que d'ouvrir sur une pièce vide |

`naive` n'est jamais listé : c'est déjà le défaut du sélecteur, et sur un tableur — le seul format
où il diffère du routage — on l'obtient en décochant « Optimiser pour les tableaux », contrôle qui
existe déjà.

**Le backend, lui, accepte le catalogue entier quel que soit le document.** Une option avancée
explicite n'est pas une suggestion, et le contrat d'API ne peut pas dépendre d'un rapport de
precheck que l'appelant n'a pas lu : un client REST qui force `qa` sur un PDF obtient ce qu'il a
demandé — et l'échec moteur mesuré qui va avec. La restriction « jamais sur un scan » est en outre
une **prudence assumée, pas un résultat** : aucune mesure n'a porté sur un document numérisé.

### 3. Ce qui est écarté, et le chiffre qui l'écarte

| template | raison mesurée |
|---|---|
| `email` | **Aucune garde de format.** `rag/app/email.py::chunk` documente « Only eml is supported » et ne vérifie rien (vérifié dans l'image déployée) : posé sur un PDF il rend `run=done`, 2 fragments, **couverture source 0,022**, premier fragment `%PDF-1.4 … /BaseFont /Helvetica`. Le refus tombe désormais à la validation du schéma, pas au chunking. |
| `book` | Découpage dégénéré, jamais favorable : 1044 fragments de **65 caractères** médians sur l'article ACL (contre 32 de 1497 pour `paper`), 30 fragments de 116 caractères sur un PDF de 8 pages (contre 9 de 332). Tranche au passage l'hypothèse Phase 11 « `paper` et `book` indiscernables » : deux documents les séparent nettement. |
| `one`, `tag`, `resume` | Aucune mesure favorable, ni en Phase 11 ni ici. Une option sans effet vérifié n'est jamais exposée. |
| `presentation`, `picture` | Le moteur se les assigne seul et **refuse qu'on les lui impose** (même garde que §1). Les exposer ferait échouer le document au lieu de l'indexer. |
| `table` | Conservé au **routage automatique** sous « optimiser pour les tableaux », pas au catalogue avancé : la case existe déjà à l'écran, le proposer une seconde fois ne changerait rien. Limite mesurée en §5.5. |
| `qa` sur PDF | Écarté **à l'écran seulement** (cf. §2) : échec moteur mesuré, 0 fragment. |

### 4. Le mécanisme anti-oubli — un index vivant, verrouillé par un test

**Décision : l'index interne des rapports de mesure**, une ligne par dossier des résultats de mesure internes — date,
ce qu'il établit, suite donnée (*exploité* / *dormant* / *clos sans suite* / *outil*) — tenue **au
moment où le rapport est produit**, jamais à l'archivage de la phase.

**Ce qui le rend vérifiable plutôt que pieux** : un test interne (non publié) échoue
dans les deux sens — un dossier produit et non indexé, une ligne d'index qui désigne un dossier
disparu. Rouge avant vert : un dossier vide ajouté sous les résultats de mesure internes fait tomber le test
avec le nom du fautif. Hermétique, aucune stack.

**Pourquoi cette forme et pas une autre** :

- *une règle de process dans CONTRIBUTING.md seule* — insuffisante : il en existait déjà une (« aucun
  rapport produit qui ne soit référencé depuis ce fichier », DoD Phase 15), et le rapport de la
  Phase 11 **était** référencé. Ce qui manquait n'était pas une consigne, c'était quelque chose qui
  casse (principe directeur n°1 de la Phase 14). La règle est quand même ajoutée à CONTRIBUTING.md §9,
  mais comme rappel du test, pas à sa place ;
- *une entrée dans le backlog interne* — mauvais fichier : il recense des **fonctionnalités**
  reportées issues des tests manuels, pas des mesures. Y mêler les deux aurait dilué les deux ;
- *un contrôle plus riche* (vérifier que chaque rapport est cité par une ADR vivante) — écarté :
  il serait rouge en permanence sur les dossiers de mise au point et les pistes écartées, donc
  ignoré. Un test rouge que personne ne peut verdir est pire que pas de test. L'invariant retenu se
  répare toujours en une ligne, ce qui est la condition pour qu'il soit réellement tenu.

Effet de bord immédiat, et c'est le but : l'inventaire a fait remonter **5 dossiers cités nulle
part** et **3 cités seulement depuis une archive de phase** — dont `diagnose-parser-config-20260807-111857`
(les clés de `parser_config` réellement acceptées par le moteur), marqué *dormant*.

## Conséquences

### 5. Dettes connues — consignées ici, corrigées ailleurs

Aucune de ces quatre n'est traitée par cette phase. Elles sont écrites pour ne pas redevenir des
découvertes accidentelles, ce qui est exactement le défaut que cette ADR corrige.

**5.1 — Le flake d'ingestion des tableurs (≈1 run sur 3) — cause confirmée le 2026-08-29, source
interne à RAGFlow, non corrigeable de notre côté.** Un `.xlsx`/`.xls` binaire échoue par
intermittence en `engine_processing_failed`, message « Failed to parse CSV and convert to Excel
Workbook » (documents 08 et 53). Le harnais le rejoue déjà (`ENGINE_TRANSIENT_RETRY_CODES`,
`run.py` (suite de mesure interne)), ce qui le rend invisible sans le corriger.

Ni l'étiquette d'origine « perte d'extension » ni la piste « clé MinIO / collision de nom »
envisagée ensuite ne tiennent : la clé de stockage est toujours résolue correctement (vérifié —
`location` retrouvé sans suffixe parasite sur les cas capturés). C'est le **contenu** qui est
corrompu avant même d'atteindre `FileService.upload_document` :

- reproduction directe (24 tentatives, harnais hors pytest) : **9/24 échecs (37,5 %)**, conforme au
  taux déjà mesuré ;
- sur un cas capturé, RAGFlow enregistre `size=5050` contre **5048 octets réels** côté source
  (checksum RAGEngine identique à l'original, donc RAGEngine n'est pas en cause). Lecture directe de
  l'objet stocké (`settings.STORAGE_IMPL.get(...)` dans `ragflow-cpu`) : deux octets `\r\n` sont
  **préfixés** devant le contenu réel, sinon intact — d'où le décalage de taille et l'échec du test
  de signature 4 octets de `_load_excel_to_workbook` ;
- chaîne d'élimination, chaque étape testée isolément et **propre en séquentiel** (0 échec sur
  ~45 tentatives cumulées) : upload direct à RAGFlow sans aucun code RAGEngine · relecture du
  document RAGEngine ayant échoué en aval (checksum/size inchangés en base) · `RagflowEngineAdapter.
  upload_document` (code de production) appelé en boucle ;
- le déclencheur est la **concurrence**, pas notre code : un `httpx.AsyncClient` **brut**, sans
  aucune classe RAGEngine, reproduit la même corruption (1/24) sous `asyncio.gather` contre l'API
  RAGFlow, et la persistance de l'échec avec `httpx.Limits(max_keepalive_connections=0)` (1/42)
  écarte la piste la plus commode (réutilisation de connexion côté client). RAGFlow sert son API via
  `app.run(...)` (`api/ragflow_server.py:165`) — le serveur de développement Werkzeug intégré à
  Flask, pas un serveur WSGI de production — cohérent avec une corruption qui n'apparaît que sous
  requêtes concurrentes ;
- signal croisé : le même écart de +2 octets (`\r\n` préfixé) a été observé indépendamment sur un
  `.jpg` (`wikimedia-declaration-independence.jpg`, `PIL.UnidentifiedImageError`,
  `rag/app/picture.py:69`), même famille de défaut sur un autre format — mécanisme générique côté
  serveur RAGFlow, pas un cas xlsx-spécifique.

**Conséquence** : pas de correctif RAGEngine possible sans patcher RAGFlow (hors périmètre, ADR-001).
Le rejeu déjà en place (`ENGINE_TRANSIENT_RETRY_CODES`) reste la mitigation correcte et suffisante.
Si une correction amont est un jour souhaitée : limiter la concurrence des requêtes RAGEngine →
RAGFlow (aujourd'hui `CELERY_DEFAULT_CONCURRENCY=4`), ou faire tourner RAGFlow derrière un serveur
WSGI de production plutôt que `app.run()` — deux changements d'infrastructure, pas de code
applicatif.

**5.2 — Les notes de présentateur d'un `.pptx` ne sont jamais lues.** Vérifié dans l'image déployée :
`deepdoc/parser/ppt_parser.py` ne parcourt que `slide.shapes`, `slide.notes_slide` n'y apparaît
nulle part. Conséquence assumée au corpus : la question `q26` reste `aucune-reponse`, le montant
qu'elle cherche vivant dans les notes. Le correctif du §1 rend le diaporama indexable ; il ne rend
pas ses notes lisibles.

**5.3 — `render_corpus_readme.py` (outil interne) est cassé.** `KeyError: 'id'` ligne 191, sur une entrée
`accepted_failures` sans identifiant — panne antérieure à cette phase (relevée en ADR-043),
revérifiée le 2026-08-29. le README du corpus interne ne peut donc pas être régénéré, et a été mis à
jour à la main.

**5.4 — Le `.eml` est déclaré « rejet moteur » alors que la mesure dit le contraire.** ~~Le manifeste
verrouille le document 36 en `accepted_failures` (« rejeté par RAGFlow »).~~ **Corrigé le
2026-09-05** : c'était RAGEngine qui posait `naive` dessus, et **c'est ce template qui était refusé**
(« file type not supported yet(pdf, xlsx, doc, docx, txt supported) »). Sans rien poser, le moteur
choisit `email` seul et rend 2 fragments propres, mots reconnus **0,949**. Même défaut que le
diaporama du §1, même correctif : `profile_for_decision` (`chunking_profile.py`) court-circuite
désormais tout `.eml` vers un profil vide, reconnu par nom de fichier (`.eml` n'a pas de
`PrecheckFormat` dédié, contrairement au diaporama — trop peu d'occurrences pour justifier une
nouvelle valeur d'énumération). `accepted_failures` du document 36 vidé au manifeste ;
`structure.status` reste `to_establish` (une seule vérification manuelle, pas la méthodologie à
quatre bras qui a figé le `.pptx`).

**5.5 — `table` perd le libellé de feuille d'un `.xlsx` multi-feuilles.** Mesuré : `naive` répète le
nom de la feuille sur chaque ligne, `table` ne le transporte pas (rappel `table` → `naive` 0,75 sur
le document 08). Aucun paramètre RAGFlow ne permet de le réinjecter — `rag/app/table.py` ne lit que
`table_column_mode`/`table_column_roles`, et `Excel.__call__` empile un `DataFrame` par feuille sans
son nom. Le contourner voudrait dire pré-traiter le classeur, donc réimplémenter une brique du
moteur (CONTRIBUTING.md §3). Limite documentée, pas un défaut à corriger.

### Favorables, chiffrées

- **Le diaporama est ingérable** : `failed`/0 fragment → `completed`/4 fragments (92/195/282
  caractères), l'échec accepté correspondant retiré du manifeste et remplacé par une fourchette.
- **Docling n'est pas élargi, et c'est un résultat** : sur un scan réel il rend **33,3 % de
  fragments dégénérés** contre 0 pour DeepDOC, pour 32,4 s contre 20,3 s ; sur un `.docx` il est à
  égalité stricte (rappel croisé 0,9875) ; sur `.pptx`, image et PDF corrompu il n'est jamais
  atteint. Le routage d'ADR-043 était déjà à la bonne portée — vérifié, plus supposé.
- **La fin de la combinatoire aveugle** : 5 templates exposés sur 13, chacun avec sa mesure, au lieu
  de 104 paires moteur × template dont la plupart n'ont jamais été éprouvées.
- **Non-régression** : suite backend **1220 → 1255** tests (+14 au correctif diaporama, +21 au
  panneau avancé), frontend **52 → 61**. Le témoin de non-régression du corpus (CV à sections,
  ADR-042/043) reste dans la fourchette du manifeste.

## Addendum — panneau avancé désactivé (2026-09-06)

Exactement le trou pressenti ci-dessous (« le retrieval n'a pas été mesuré ») : sur `real-gdpr-fr.pdf`
ingéré avec `chunk_method_override="laws"`, un fragment de **41 264 caractères** (~8000 tokens) s'est
retrouvé dans un lot de reranking aux côtés d'une trentaine d'autres — `CUDA out of memory` chez
Xinference, 6,43 Gio demandés pour ce seul lot, budget de 12 Go dépassé. Les mesures de cette ADR
portaient sur la *structure* du découpage (nombre de fragments, taille médiane, couverture) — jamais
sur l'interaction d'un fragment-extrême avec le budget VRAM du reranking en aval. Un plafond sur le
*nombre* de candidats reranked (`RERANK_CANDIDATE_POOL_SIZE`, `app/core/config.py`) a été posé le même
jour mais ne suffit pas : la cause est la *longueur* d'un fragment isolé, pas le nombre de candidats.

**Décision utilisateur explicite** : plutôt que garder l'option en connaissant ce risque non couvert,
le panneau « Paramètres avancés » est désactivé pour cette version — `advancedChunkMethodsFor`
(`frontend/src/api/precheck.ts`) retourne toujours `[]`, et `chunk_method_override` non-`null` est
rejeté (422) à la soumission d'une nouvelle décision (`DocumentPrecheckDecisionUpdate`, `app/domain/
schemas/document.py`). Un document déjà ingéré avec cette option reste rejouable tel quel — seule la
soumission d'une **nouvelle** décision est bloquée (`workers/tasks/ingestion.py` reconstruit la
décision stockée sans repasser par ce garde-fou).

**Non touché, délibérément** : `chunking_profile.py` sait toujours appliquer un `chunk_method_override`
si on lui en passe un — c'est la voie d'*entrée* qui est coupée, pas le mécanisme. Réactivation
V2 envisageable une fois qu'une limite de longueur par fragment est posée en amont du reranking
(candidat non résolu ici, cf. § suivant) — pas seulement le plafond de candidats déjà en place.

## Correction (2026-09-13) — le rejeu n'est pas en place en production

**§5.1 affirme que « le rejeu transitoire déjà en place reste la mitigation correcte et
suffisante ». C'est faux pour la production : ce rejeu n'existe que dans le harnais de
mesure.** `ENGINE_TRANSIENT_RETRY_CODES` et le rejeu qui s'appuie dessus (`retry_transient_failures`,
`_ingest_with_transient_retry`) vivent dans `run.py` (suite de mesure interne) (~47-48, ~505-516)
et `test_office.py` (suite de mesure interne) (~62-90) — un outillage de campagne de mesure, jamais
exécuté en dehors d'un `pytest` du corpus. Le chemin de production, `_poll`
(`backend/app/workers/tasks/ingestion.py`, ~1077-1084), code en dur `retryable=False` pour
`engine_processing_failed` : un document réel qui subit ce flake échoue et **reste en échec**, sans
aucun rejeu.

Les deux correctifs d'infrastructure déjà proposés par cette même ADR (§5.1, non appliqués ici,
rappelés pour ne pas se reperdre comme `MATRIX-SUMMARY.md` — c'est précisément le mécanisme que
cette ADR corrige par ailleurs) restent les pistes à instruire :

1. limiter la concurrence des requêtes RAGEngine → RAGFlow (aujourd'hui `CELERY_DEFAULT_CONCURRENCY=4`) ;
2. faire tourner RAGFlow derrière un serveur WSGI de production plutôt que `app.run()`.

## À revisiter

- **Borner la longueur d'un fragment avant reranking, pas seulement leur nombre.** Xinference charge
  le reranker avec `max_length=model_family.max_tokens` (proche du maximum natif du modèle,
  `sentence_transformers/core.py::load`) — aucun mécanisme actuel ne tronque un fragment-extrême
  avant qu'il n'entre dans un lot. Deux pistes non explorées : un plafond de longueur à l'ingestion
  (rejeter/scinder un fragment au-delà d'un seuil, quel que soit le template) ou une réduction du
  `batch_size` effectif du reranker — mais RAGFlow appelle le service externe directement, RAGEngine
  ne contrôle pas le corps de cette requête (ADR-001, pas de contournement des briques internes).
- **Le retrieval n'a pas été mesuré.** Tous les verdicts de cette ADR portent sur la *structure* du
  découpage (nombre de fragments, taille médiane, couverture, rappel croisé). Savoir si `laws` sur
  un texte réglementaire *retrouve mieux* que `naive` demande une campagne de pertinence — c'est la
  seule chose qui autoriserait un jour à changer le défaut plutôt qu'à offrir une option.
- **`diagnose-parser-config-20260807-111857`** est marqué *dormant* dans l'index : les clés de
  `parser_config` acceptées par le moteur n'ont jamais été instruites au-delà de la Phase 11.
- Si l'index interne des rapports de mesure devient pénible à tenir, c'est le signal que les
  rapports de mise au point (statut *outil*) devraient être écrits ailleurs que dans
  les résultats de mesure internes — pas que l'invariant doit sauter.

## Clôture (2026-09-21) — voir ADR-068

Le trou signalé par la correction du 2026-09-13 (« le rejeu n'est pas en place en production ») est
comblé : **ADR-068** pose le rejeu dans l'adaptateur (`RagflowEngineAdapter.upload_document`,
vérification d'intégrité + rejeu borné, idempotent par suppression avant nouvel essai), tranche les
deux pistes d'infrastructure listées ci-dessus (concurrence Celery : gain marginal désormais plus
faible, non chiffré faute de mesure possible cette session ; serveur WSGI/Go : écarté sur preuve
directe, pas seulement par prudence — cf. ADR-068 §4), et documente précisément ce qui reste à
vérifier contre un RAGFlow réel.
