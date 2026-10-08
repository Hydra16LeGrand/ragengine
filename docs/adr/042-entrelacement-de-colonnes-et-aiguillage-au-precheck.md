# ADR-042 — Entrelacement de colonnes : détecté au precheck, arbitré à l'écran

**Date :** 2026-08-22 · **Statut :** accepté — restreint la proposition d'ADR-031, sans la révoquer

## Résumé

- **Mesuré, pas déduit** : sur un PDF à deux colonnes, `DeepDOC` lit le texte **ligne par ligne
  en traversant la gouttière**. Il tombe sur la borne d'entrelacement calculée depuis le PDF
  source ; `Plain Text` tombe près de la borne de lecture correcte.
- **Cause établie au code source de l'instance** : la chaîne PDF du moteur se termine par un tri
  global par altitude, et la passe qui trierait par colonne est **commentée** juste à côté. Rien
  ne se règle par paramètre. Même famille de cause qu'ADR-038.
- **La métrique historique était aveugle au sujet qu'elle nomme** : `column_interleaving_rate`
  compte à l'identique un saut de ligne légitime et un saut de colonne fautif. Remplacée ici par
  `column_switch_rate`, auto-étalonnée sur le PDF source.
- **Correctif côté RAGEngine, pas côté moteur** : le precheck compte les pages à colonnes, et
  l'écran d'aiguillage cesse de proposer l'analyse de mise en page — **uniquement** quand aucun
  tableau n'est détecté. Quand les deux signaux coexistent, les deux gains s'opposent réellement
  et l'écran expose l'arbitrage au lieu de le trancher.
- **Ce que ça coûte, dit ouvertement** : le mode direct perd les positions de fragment sur 100 %
  des fragments (citations, page d'origine, ordre du panneau de consultation). Arbitrage assumé :
  un texte dont les phrases sont entremêlées est faux pour la recherche, une position manquante
  n'est qu'une gêne de restitution.
- **Ce qui reste cassé** : un document numérisé à colonnes. `is_likely_scanned` y impose
  `DeepDOC`, et le mode direct n'y produit rien du tout (0 fragment, 0 caractère, vérifié).
  Plafond figé au manifeste, échec accepté.

## Contexte

Remontée la plus transverse de la campagne de tests manuels (les remarques internes de test,
analysée dans l'analyse interne des tests manuels §6.1) : « quand un PDF contient deux
sections de texte côte à côte, le texte est extrait ligne par ligne en entrelaçant les colonnes ».
L'analyse §6.1 posait l'hypothèse — le défaut viendrait du réglage par défaut d'ADR-031, qui fait
cocher l'analyse de mise en page sur tout PDF — et demandait explicitement de la mesurer avant de
corriger. §9 la listait parmi ce qui n'avait **pas** été reproduit.

### La métrique existante ne mesurait pas ce qu'elle nomme

`measure_chunking_quality.py::column_interleaving_rate` compare chaque paire de mots consécutifs
d'un fragment à une référence de paires « légitimes », construite en ne retenant que les mots
contigus **sur une même ligne visuelle**. Une paire (dernier mot d'une ligne, premier mot de la
suivante) n'y figure donc jamais — alors que c'est une adjacence parfaitement normale de l'ordre
de lecture. Conséquence : un saut de ligne correct et un saut de colonne fautif sont comptés à
l'identique. D'où le plancher de bruit de ~0,30 relevé au manifeste **sur un document
mono-colonne**, c'est-à-dire sur un document qui n'a aucune colonne à entrelacer.

Les chiffres d'ADR-029 sur ce point (« entrelacement : `Plain Text` meilleur de 1,6 à 28,8 pt »)
ne sont donc pas faux, ils sont **indécidables** : ils mélangent deux phénomènes.

### La métrique posée ici

`column_switch_rate` (`column_reading_order.py` (outil interne)) : la part des mots consécutifs qui
changent de colonne, calculée sur les seuls **mots d'ancrage** — ceux dont toutes les occurrences
du document tombent dans une seule et même colonne, donc dont la provenance est certaine sans
aucun alignement de séquences ni hypothèse sur la tokenisation du moteur.

Elle est **auto-étalonnée** : les deux bornes de l'échelle sont calculées sur le PDF source
lui-même — lecture colonne par colonne d'un côté, lecture ligne par ligne de l'autre. Aucun seuil
n'est posé à la main, et un mode moteur ne se juge pas dans l'absolu mais par sa position entre
les deux bornes.

## Mesure

Trois campagnes, deux ingestions par mode, dataset RAGFlow jetable créé pour l'occasion (aucune
base de l'utilisateur touchée). Résultats bruts dans
`column-reading-order-*` (mesure interne).

| document | lecture correcte | lecture entrelacée | `DeepDOC` | `Plain Text` |
|---|---:|---:|---:|---:|
| `02-pdf-multi-column-cv-fr.pdf` (1 p., synthétique) | 0,021 | 0,327 | **0,283** | **0,057** |
| `real-notice-2042.pdf` (40 p., réel) | 0,014 | 0,167 | **0,168** | **0,021** |

`DeepDOC` tombe **exactement** sur la borne d'entrelacement de la notice (0,168 contre 0,167), et
à 88 % du chemin vers elle sur le CV. Les deux ingestions de chaque mode donnent la même valeur à
0,001 près : ce n'est pas du bruit.

Contre-mesure indispensable, sur `04-pdf-scanned-fullpage-image.pdf` : `Plain Text` produit
**0 fragment et 0 caractère**, `DeepDOC` en produit 376. Sur un scan il n'y a pas de couche texte
à lire — le mode direct n'y est pas un choix, c'est une perte totale.

Vérification indépendante, sans ingestion : le flux de contenu lu par `pypdf` — c'est-à-dire
exactement ce que `Plain Text` restitue — vaut 0,020 sur la notice, 0,095 sur le cerfa 2035 et
0,094 sur l'article ACL, contre des bornes d'entrelacement à 0,167 / 0,145 / 0,447. L'avantage du
mode direct n'est donc pas un artefact du corpus synthétique : les PDF réels portent bien un flux
de contenu ordonné colonne par colonne.

## Cause, établie dans le code source de l'instance déployée

`rag/app/naive.py` — le seul chemin qu'emprunte RAGEngine — termine sa chaîne PDF ainsi :

```python
tbls = self._extract_table_figure(True, zoomin, True, True)
self._naive_vertical_merge()
self._concat_downward()
# self._final_reading_order_merge()
```

- `_concat_downward()` est réduit dans cette version à `Recognizer.sort_Y_firstly(self.boxes, 0)`
  suivi d'un `return` : un tri global par altitude, c'est-à-dire **un ordre ligne par ligne**.
- `_final_reading_order_merge()` — qui trie précisément par `(page, col_id, top)`, l'ordre
  recherché — est **commentée**.
- `_naive_vertical_merge()` a son propre regroupement par colonne commenté lui aussi
  (`grouped[(b["page_number"], "x")]` au lieu de `col_id`).
- `_assign_column()` s'exécute pourtant bel et bien et calcule un `col_id` par boîte — le journal
  du moteur le dit à chaque ingestion (`Global column_num decided by majority: 3`). Ce `col_id`
  n'est jamais utilisé pour ordonner quoi que ce soit.

Le moteur **sait** donc découper une page en colonnes, et **n'utilise pas** ce qu'il sait pour
l'ordre de lecture. Aucun paramètre de `ParserConfig` n'y donne accès : la seule clé de mise en
page, `layout_recognize`, choisit entre `DeepDOC`, son court-circuit `Plain Text` et des parseurs
externes (MinerU, PaddleOCR, OpenDataLoader, SoMark) qui exigent tous un fournisseur déclaré.

C'est la même famille de cause qu'ADR-038 (`Recognizer.sort_Y_firstly` déplaçant un marqueur de
liste), sur un autre symptôme.

## Décision

**Le precheck compte les pages à colonnes ; l'écran d'aiguillage cesse de proposer l'analyse de
mise en page sur un document à colonnes *sans tableau*.**

1. `PrecheckReport.two_column_page_count` (`app/ingestion/precheck/columns.py`). Une gouttière
   n'est retenue que si elle est assez large *et* assez haute pour traverser la zone de texte, et
   si des lignes la bordent des deux côtés. **Seules les pages coupées en exactement deux bandes
   sont comptées** : c'est ce qui écarte les tableaux, qui se présentent au même détecteur en
   quatre ou cinq bandes. Vérifié sur le corpus — présent sur 02, 47, 48, 49, 50 ; silencieux sur
   les quatre documents à tableau et sur tous les mono-colonne.
2. `suggestedDecision` ne propose plus `heavy_ocr` quand `twoColumnPageCount > 0` **et**
   `tableCount === 0`.
3. **Quand les deux signaux coexistent, rien ne change** : seul le mode complet reconnaît les
   tableaux, seul le mode direct respecte les colonnes, et la mesure ne les départage pas. La
   proposition d'ADR-031 s'y applique telle quelle, et l'écran affiche l'arbitrage au lieu de le
   trancher en silence.
4. **Le contrat d'API ne bouge pas.** `heavy_ocr` reste `false` par défaut au schéma, et
   `profile_for_decision` reste inchangé : un scan part en `DeepDOC` quelle que soit la décision
   reçue. C'est une proposition d'interface, comme ADR-031.

## Alternatives écartées

- **Réactiver `_final_reading_order_merge()` dans l'image dérivée** (une troisième divergence,
  après ADR-030 et ADR-035). Simulé en rejouant l'algorithme exact de `_assign_column` du moteur
  (KMeans sur `x0`, tolérance d'indentation de 12 %) puis son tri `(page, col_id, top)` : le CV
  passerait de 0,327 à 0,245 et l'article ACL de 0,447 à 0,179, pour des lectures correctes à
  0,021 et 0,011. Le gain est réel mais très loin du compte, parce que `_assign_column` regroupe
  sur le bord gauche des boîtes : dans une page à deux colonnes, les lignes indentées de la
  colonne gauche partent dans un cluster à elles. **Simulation sur lignes de texte, pas sur les
  boîtes de mise en page de DeepDOC** — directionnelle, pas probante. Elle suffit néanmoins à ne
  pas engager une divergence d'image à revalider à chaque montée de version pour ce résultat-là.
- **Un parseur de mise en page externe** (MinerU, PaddleOCR, OpenDataLoader, SoMark). Chacun
  suppose un fournisseur déclaré et un service supplémentaire ; les modèles de mise en page de
  cette classe ne tiennent pas dans le budget VRAM restant à côté de BGE-M3 et du reranker
  (CONTRIBUTING.md §2).
- **Réordonner les fragments côté RAGEngine.** Interdit par ADR-001 et CONTRIBUTING.md §3 : ce serait
  réimplémenter la brique de mise en page du moteur.

## Conséquences

**Assumées :**

- **Les positions de fragment disparaissent.** Mesuré sur toutes les campagnes : part de
  fragments positionnés **1,00 sous `DeepDOC`, 0,00 sous `Plain Text`**, sans exception. En aval,
  cela retire la page d'origine d'une citation, tout repérage dans le document source, et rend
  l'ordre du panneau « Voir les fragments » non pertinent (`list_document_chunks` n'a plus rien à
  trier — cause déjà établie en ADR-029 §1). **Le détecteur n'arbitre pas ce compromis, il
  l'ignore** : il ne regarde que colonnes et tableaux. Arbitrage explicite, à contester si
  besoin — un texte dont les phrases de deux colonnes sont entremêlées est *faux* pour la
  recherche, une position manquante n'est qu'une gêne de restitution.
- **Les coupures en plein milieu de phrase reviennent** sur les documents concernés (33-60 % des
  fragments, chiffres d'ADR-029), et les tableaux n'y sont plus reconnus (fait établi 8).
- **Un scan à colonnes reste cassé.** `is_likely_scanned` y impose `DeepDOC`, et le mode direct
  n'y produit rien. Échec accepté, plafond figé au manifeste (document 02, scénario `heavy_ocr`,
  `max_column_switch_rate: 0.32` pour 0,283 observé), verrouillé contre l'aggravation par
  `test_adr042_l_entrelacement_de_colonnes_sous_deepdoc_reste_a_son_niveau`.

**Favorables, chiffrées :**

- **Temps d'ingestion divisé par 12** sur `real-notice-2042.pdf` : 361 s sous `DeepDOC` contre
  31 s sous `Plain Text`, deux ingestions chacune. Sur ce document, le mode lent produit en outre
  **moins de texte** (272 349 caractères contre 291 740) et un ordre de lecture pire. L'arbitrage
  latence/qualité d'ADR-031 ne s'applique donc pas ici : sur un document à colonnes, le mode
  complet est plus lent *et* moins bon.
- **Exposition au quota de l'API de vision réduite.** Établi par `ingestion_jobs.error` :
  `real-cerfa-2035-sd.pdf` a échoué le 2026-08-22 en `engine_processing_failed` /
  `429 RESOURCE_EXHAUSTED` avec `heavy_ocr: true`, et **abouti** le 2026-08-16 avec
  `heavy_ocr: false`. Seul le mode complet découpe les figures et les envoie décrire : le mode
  direct n'appelle jamais l'API. La proposition d'ADR-031 multipliait donc mécaniquement
  l'exposition au quota — cause déjà identifiée derrière quatre échecs de la campagne
  (l'analyse interne des tests manuels §2).

## À revisiter

- Si RAGFlow réactive `_final_reading_order_merge()` en amont, remesurer avant de retirer
  l'échec accepté : la simulation ci-dessus dit que la réactivation seule ne suffirait pas.
- Le cas « colonnes **et** tableaux » (notice 2042, cerfa 2035, article ACL) reste tranché par
  l'humain à chaque document. S'il s'avère en usage que l'ordre de lecture y prime toujours, la
  condition `tableCount === 0` peut sauter — mais ce serait une décision produit, pas un
  résultat de mesure, et cette ADR ne la prend pas.
