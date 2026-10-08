# ADR-033 — Le découpage parent/enfant est réel : c'est sa restitution à l'écran qui manquait

**Date :** 2026-08-12 · **Statut :** accepté · **Portée :** interface de consultation des
fragments, libellé de l'option de precheck · **Ne change rien à l'indexation**

## Contexte

Un retour d'usage sur un PDF de 7 pages indexé avec `use_parent_child` : la liste des fragments
affiche **98 entrées, longueur médiane 72 caractères, 80 sur 98 sous 150 caractères, la plus
courte à 8**. Deux autres documents de la même base, sans l'option, en affichent 3 et 4. Lu depuis
l'interface, cela ressemble à un découpage cassé — et la question posée était légitime :
l'agrégation vers le fragment parent existe-t-elle vraiment sur cette version du moteur, ou
l'option ne fait-elle que découper plus petit ?

La question se posait d'autant plus que la fonctionnalité a été livrée par un **contournement** :
la clé documentée `parser_config.parent_child.use_parent_child` est un no-op silencieux sur le
chemin document, et RAGEngine pose la clé plate `children_delimiter` via `parser_config.ext`
(`backend/app/ingestion/chunking_profile.py`).

## Vérification — code du moteur *et* recherche réelle, pas l'un ou l'autre

**Ce que fait le moteur** (lu dans le conteneur `ragflow-cpu` déployé) :

1. `rag/app/naive.py::chunk()` lit la clé **plate** `parser_config.children_delimiter` — c'est
   bien celle que pose le contournement — et découpe chaque fragment en enfants, chacun portant
   le texte de son parent dans un champ `mom`.
2. `rag/svr/task_executor.py::insert_chunks()` en dérive un `mom_id` (hachage du texte parent),
   **insère le fragment parent comme document à part entière** avec `available_int = 0`, et
   étiquette chaque enfant de ce `mom_id`.
3. `rag/nlp/search.py::retrieval()` filtre sur `available_int = 1` — traduit côté Elasticsearch en
   `must_not(range available_int < 1)`, donc les enfants (qui n'ont pas le champ) passent, les
   parents non : **seuls les enfants sont interrogés**.
4. `rag/nlp/search.py::retrieval_by_children()` regroupe les enfants retrouvés par `mom_id`, va
   chercher le parent **par identifiant** (`dataStore.get`, qui ne repasse pas par le filtre) et
   **remplace le groupe d'enfants par le parent** dans les résultats. Appelée par
   `api/apps/restful_apis/chunk_api.py:411`, c'est-à-dire par `POST /retrieval` — le seul endpoint
   de recherche que RAGEngine utilise (`providers/engine/ragflow/client.py::search`).

**Ce que montre l'index réel** (document du retour d'usage, 98 fragments) : 94 enfants portant un
`mom_id`, médiane 64,5 caractères, plus court 5 ; **4 parents** de 805, 2169, 2218 et 2245
caractères, `available_int = 0`. Les 98 « fragments » ne sont donc pas 98 unités de recherche :
ce sont 94 sondes et 4 passages.

**Ce que rend une recherche réelle** sur ce document : les identifiants restitués sont exactement
ceux des parents (805 et 2170 caractères), jamais ceux des enfants.

**A/B contrôlé** sur un document du corpus de test (`11-pdf-long-multipage.pdf` (corpus interne),
base jetable créée puis supprimée), même fichier versé deux fois dans la même base :

| | fragments listés | dont interrogeables | médiane | fragments rendus par la recherche |
|---|---|---|---|---|
| sans l'option | 2 | 2 (316 et 2340 car.) | 1328 | les 2 fragments, tels quels |
| avec l'option | 19 | 17 (médiane 38 car., min 14) | 38 | **le parent de 2340 car.** |

Le point décisif est dans la dernière colonne rapprochée de la première ligne : **les 2 parents
créés par l'option sont, au caractère près, les 2 fragments que le document produit sans elle**
(316 et 2340). L'option n'appauvrit donc pas l'extrait rendu — elle ajoute une couche de sondes
au-dessus du même découpage, et la recherche remonte au même passage qu'avant.

**Verdict : l'agrégation parent fonctionne.** Le contournement n'est pas un découpage plus fin
sans bénéfice ; c'est la fonctionnalité complète. Rien à retirer du catalogue — le précédent
GraphRAG `ner` (ADR-023, commits `009c245`/`45a850e`) ne s'applique pas ici, précisément parce
qu'ici la mesure a montré l'inverse.

## Le vrai défaut : l'écran ne dit rien de tout cela

Le moteur marque ses parents `available_int = 0`. L'API de listage des fragments
(`GET /datasets/{id}/documents/{doc}/chunks`) n'expose **ni `mom_id` ni aucun marqueur de
filiation** — champs réellement renvoyés, relevés sur l'instance : `id`, `content`, `document_id`,
`dataset_id`, `docnm_kwd`, `positions`, `image_id`, `important_keywords`, `questions`,
`available`, `tag_kwd`. Le seul signe visible d'un parent y est donc `available = false`,
indiscernable d'un fragment désactivé à la main.

L'interface affichait en conséquence : les 4 passages réellement restitués sous une étiquette
**« Désactivé »** (tonalité d'avertissement), les 94 sondes sans aucune mention de leur rôle, et
aucune explication de l'ensemble. La lecture spontanée — « le découpage est cassé » — était la
seule disponible.

## Décision

1. **L'API expose la nature du découpage, pas l'interface ne la devine.**
   `DocumentChunksMeta.uses_parent_child` est ajouté à `GET /api/v1/documents/{id}/chunks`, lu
   depuis `Document.precheck_decision` **en base** — la décision de l'utilisateur est le seul
   signal non ambigu, puisque le moteur n'en donne aucun. Tolérant par construction : décision
   absente, clé absente ou valeur inattendue ⇒ `false`, comportement d'avant la Phase 13. Une
   liste de fragments ne doit jamais échouer sur un défaut de métadonnée.
2. **Le vocabulaire de l'écran nomme les rôles, pas l'état moteur.** Sur un document en
   parent/enfant, un fragment `available = false` est étiqueté « Passage restitué » (tonalité
   accent, avec l'explication en infobulle) et les autres « Repérage » ; « Désactivé » n'apparaît
   plus que sur un document **sans** l'option, où il garde son sens d'origine.
3. **Une note d'explication précède la liste** dès que le document est en parent/enfant, et dit
   les deux natures de fragments et laquelle la recherche renvoie.
4. **Le libellé de l'option dit la conséquence visible avant qu'on ne coche**, avec le chiffre
   mesuré (19 fragments listés au lieu de 2 sur le document d'essai, dont 17 de repérage) et le
   fait qui rassure : l'extrait rendu reste le même qu'avec le découpage habituel.

## Ce qui a été explicitement écarté

- **Filtrer les parents hors de la liste.** Ils sont le contenu réellement restitué : les cacher
  rendrait l'écran cohérent au prix de la vérité, et l'utilisateur qui veut vérifier ce que la
  recherche va rendre n'aurait plus rien à lire.
- **Demander `mom_id` au moteur** en touchant à son code d'API. ADR-001 : RAGFlow est consommé tel
  qu'il est. La décision de precheck, déjà persistée, répond à la même question sans y toucher.
- **Un second contournement** pour enrichir la réponse du listage. Le premier était justifié par
  un bug moteur avéré et une clé libre prévue pour cela (`parser_config.ext`) ; ici rien n'est
  cassé, seule la présentation manquait.

## Conséquences

- Un client REST qui ignore `uses_parent_child` n'est pas cassé : champ additif, défaut `false`.
- L'interface distingue désormais deux situations qu'elle confondait ; en contrepartie, elle fait
  confiance à la décision persistée. Un document dont la décision aurait été perdue retomberait
  sur l'ancien affichage (« Désactivé ») — dégradation lisible, jamais une erreur.
- `frontend/src/test/setup.ts` installe `HTMLDialogElement.prototype.showModal/close`, absents de
  jsdom : sans eux, **tout** test montant un composant à travers `components/Dialog` échoue sur une
  pile React qui n'accuse pas la vraie cause. Dette payée ici parce que c'est le premier test de
  modale du dépôt (ADR-032).

## Addendum — filtre « passages restitués » opt-in (2026-09-06), et son bug de pagination (2026-09-07)

Ne contredit pas « Ce qui a été explicitement écarté » ci-dessus : ce qui était rejeté, c'est de
**masquer les parents par défaut** (l'écran mentirait par omission sur ce que la recherche renvoie).
Le filtre livré le 2026-09-06 est **opt-in** — décoché par défaut, l'utilisateur choisit
explicitement de ne voir que les passages restitués — une demande différente, jamais celle écartée
ici.

**Bug réel trouvé le 2026-09-07** : la première version filtrait la page déjà chargée côté client,
en gardant la pagination calculée sur l'ensemble complet (parents + enfants). Deux défauts en
découlaient, tous deux signalés par l'utilisateur en conditions réelles : le total/nombre de pages
affiché ne correspondait plus à ce qui s'affichait réellement une fois le filtre actif, et une page
dont le lot ne contenait que des enfants rendait un écran vide sans qu'aucune page suivante ne soit
nécessairement meilleure.

**Corrigé** : `DocumentService.list_chunks` (`parents_only`, `app/services/documents.py`) récupère
tout le document par lots de 100 (plafond RAGFlow) quand le filtre est actif, filtre en mémoire, puis
repagine sur l'ensemble filtré — `total`/`page` reflètent alors toujours ce qui s'affiche
réellement. Coût assumé (plusieurs appels moteur au lieu d'un seul) : une consultation occasionnelle,
pas un chemin chaud, et RAGFlow ne sait pas filtrer côté serveur (rien à contourner sans réimplémenter
sa brique, ADR-001). `chunking_profile.py` n'est pas concerné — c'est la lecture des fragments déjà
indexés qui change, pas leur découpage.
