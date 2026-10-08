# ADR-038 — Marqueur de liste numérotée déplacé : limite de tri des boîtes OCR DeepDOC, non corrigible côté RAGEngine

**Date :** 2026-08-15 · **Statut :** constat établi, non corrigé (ADR-001) · **Portée :**
documents à interligne serré, mise en page `DeepDOC`

## Contexte

Retour d'usage sur `AI Engineer - Hiring resources.pdf` (base TEST3, lecture seule,
`use_parent_child: true`) : un fragment enfant affiche « 2. Fact lookup accuracy Aggregation
correctness (exact match / tolerance)**3.** » — le marqueur « 3. » est passé de tête de son item
à la fin de l'item précédent, au lieu de l'ordre attendu du PDF source (« 2. … / 3. … / 4. … »).

Deux causes possibles à trancher empiriquement : (a) une limite de l'analyse de mise en page
DeepDOC elle-même (non corrigible sans réimplémenter DeepDOC, ADR-001), ou (b) un artefact du
découpage `children_delimiter="\n"` que pose `backend/app/ingestion/chunking_profile.py`
(potentiellement corrigible côté RAGEngine).

## Investigation

**Code lu dans le conteneur `ragflow-cpu` déployé** :
- `rag/nlp/__init__.py::split_with_pattern` — le découpage enfant est un `re.split` à groupe
  capturant qui recompose `[texte, délimiteur]` dans l'ordre d'origine : structurellement incapable
  de déplacer du contenu au-delà d'une frontière de split.
- Le découpage enfant s'applique en aval de `naive_merge(sections, …)` (`rag/app/naive.py`) — les
  `sections` viennent du parseur DeepDOC, **avant** toute notion de `children_delimiter`. Si l'ordre
  y est déjà faux, le découpage enfant ne fait qu'hériter la corruption, il ne peut ni la créer ni
  la corriger.

**Reproduit sur un document synthétique** (100 % fictif, `hanging-indent-tight.pdf` — liste
numérotée à retrait suspendu, interligne serré ≈1,05× la taille de police) ingéré **directement**
contre RAGFlow (dataset jetable, sans passer par RAGEngine), `chunk_method=naive`,
`layout_recognize=DeepDOC`, **sans aucun `children_delimiter`** — donc avant même que le
contournement RAGEngine entre en jeu :

```
"...Retrieval quality metrics evaluated across the full fictive benchmark suite1.
Fact lookup accuracy2.
3.Aggregation correctness (exact match / tolerance)
Citation faithfulness (answers supported by cited evidence)4.
5. Latency budget under production load..."
```

Les marqueurs sont déjà déplacés **avant tout découpage enfant** — même classe de défaut que celui
remonté sur le document réel. En activant ensuite `parent_child` + `children_delimiter="\n"` via
`parser_config.ext` (exactement le payload posé par `chunking_profile.py`), le fragment parent
(texte non découpé) porte déjà la même corruption ; les enfants ne font qu'hériter cet ordre déjà
cassé.

Une variante à interligne confortable (1,4×) sur le même contenu **ne reproduit pas** le défaut —
cohérent avec `Recognizer.sort_Y_firstly` (`deepdoc/vision/recognizer.py`), qui reclasse une boîte
OCR par `x0` dès que l'écart vertical de `top` entre deux boîtes tombe sous un seuil
(`mean_height/3`) : un interligne serré fait collisionner deux lignes physiques dans le même
cluster de tri, et le marqueur (boîte étroite, x0 le plus à gauche) peut se retrouver trié après le
corps de texte de la ligne suivante.

Confirmé par ailleurs (lecture seule) que le `parser_config` du document réel
(`341f615695d511f18fb883e7703bd0a4`) est bien `layout_recognize=DeepDOC` +
`parent_child.use_parent_child=true` + `children_delimiter="\n"` — exactement le profil reproduit
ici.

**Verdict : (a).** Le déplacement existe dans le flux `sections` que retourne le parseur DeepDOC
(tri des boîtes OCR), avant toute variable que `chunking_profile.py` contrôle.

## Décision : pas de correctif côté RAGEngine

Le seul levier RAGEngine (`children_delimiter`) agit strictement en aval du tri de boîtes OCR
défaillant : changer sa valeur ne peut ni causer ni corriger ce symptôme. Corriger réellement
demanderait de modifier l'algorithme de tri dans `deepdoc/vision/recognizer.py`/`pdf_parser.py`, à
l'intérieur du conteneur RAGFlow — interdit par l'ADR-001. **Aucune modification appliquée** à
`backend/app/ingestion/chunking_profile.py` ni ailleurs.

## Conséquences

- Une liste numérotée à interligne serré peut afficher un marqueur déplacé en fin de l'item
  précédent, avec ou sans `use_parent_child` — ce n'est pas spécifique au découpage parent/enfant,
  seulement plus visible dessus puisque chaque item y devient un fragment séparé plutôt qu'une
  portion d'un bloc plus long.
- Rejoint la famille de limites déjà documentée en ADR-023/ADR-029/ADR-031 : la qualité de
  reconnaissance de mise en page de DeepDOC a des angles morts connus, non réimplémentables côté
  RAGEngine (ADR-001).
- Non re-testé au-delà de la version RAGFlow actuellement déployée (`v0.26.4`) : une mise à jour
  moteur de l'algorithme de tri des boîtes pourrait corriger ce point sans action côté RAGEngine.

## Addendum (2026-08-16) — la variante « confortable » n'est pas un témoin propre

Le harnais de non-régression (Phase 14, Étape 1) a rejoué ce même contenu, en français, via la
chaîne RAGEngine complète (`use_parent_child` + `children_delimiter`, au lieu d'un appel direct à
RAGFlow) : la variante à interligne confortable (1,4×, `41-pdf-list-hanging-indent-loose.pdf`)
**perd elle aussi deux marqueurs sur cinq**, alors que l'investigation ci-dessus concluait qu'elle
ne reproduisait pas le défaut.

**Pas une contradiction, un jeu de conditions différent** : l'investigation d'origine portait sur
un document anglais ingéré directement contre RAGFlow ; celle-ci porte sur le document français du
corpus, passé par la façade RAGEngine. Les retours à la ligne ne tombent pas aux mêmes endroits
selon la langue et le moteur de rendu, donc les collisions de `sort_Y_firstly` (seuil
`mean_height/3`) non plus — le mécanisme identifié tient, la marge de sécurité qu'on lui avait
prêtée était trop optimiste.

**Décision : affaiblir la conclusion plutôt que chasser un témoin parfait.** Régénérer une
variante à interligne encore plus large ne garantirait rien : le mécanisme dépend des métriques de
rendu (police, langue, moteur), pas d'un seuil universel qu'on pourrait viser une fois pour toutes.
La conclusion de ce document est donc corrigée : **un interligne large réduit fortement le risque,
il ne l'élimine pas.** Aucun seuil n'est « sûr » à 100 % ; c'est cohérent avec le reste de cette
ADR — non corrigible côté RAGEngine (ADR-001), lié à un algorithme de tri de boîtes OCR interne.

Le document 41 reste au corpus comme témoin **« aggravation seulement »** (la suite de mesure interne,
manifeste) : verrouillé sur son niveau de perte actuel (2 marqueurs sur 5), pas sur zéro. Une
régression au-delà de ce niveau reste détectée ; l'absence totale de défaut ne l'est plus, parce
qu'elle n'a jamais été garantie.
