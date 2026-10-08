# ADR-056 — Filtres serveur `status`/`theme` sur la liste des documents

**Date :** 2026-09-13 · **Statut :** accepté · **Origine :** audit dette technique 2026-09, motif A

## Résumé

- **Décision** : `status`/`theme` (correspondance exacte) deviennent des paramètres de requête
  optionnels de `GET .../documents`, filtrant en base via `DocumentListParams(PaginationParams)`.
  Additif — `/v1` inchangé pour un client qui les ignore.
- **Pourquoi une sous-classe et pas un second `Query()`** : FastAPI 0.140.7 casse le dépliage du
  modèle de pagination dès qu'un second paramètre `Query()` est posé à côté (constaté
  empiriquement, pas dans la doc FastAPI — encadré plus bas).
- **Trois usages frontend, trois solutions**, jamais un filtre client sur une page tronquée :
  pagination réelle pour `DocumentsPanel` (curseur, « Charger plus ») ; parcours borné de tous les
  curseurs `status=indexed` pour le sélecteur du Playground, avec `truncated` affiché si le
  plafond (1000 documents) est atteint ; `UploadPanel` hors périmètre (angle mort déjà assumé,
  non touché).
- Les deux clés de cache littérales `['knowledge-bases']` relevées par l'audit deviennent
  `knowledgeBaseKeys.all`, déjà exporté ailleurs.

## Contexte

`GET /v1/knowledge-bases/{kb_id}/documents` ne pagine que par curseur (`limit`/`cursor`,
`DEFAULT_PAGE_SIZE=50`). Trois écrans du frontend appelaient cette route sans jamais suivre
`next_cursor`, puis filtraient ou comptaient côté client sur cette seule première page :
`DocumentsPanel.tsx` (menu et filtre de thème), et `PlaygroundPage.tsx` → `RetrievalSettings.tsx`
(sélecteur de documents interrogeables, restreint aux `indexed`). Au-delà de 50 documents dans une
base — le premier versement réel d'une base d'entreprise — un document devenait invisible dans le
menu de thème, impossible à sélectionner pour restreindre une recherche, et les décomptes dérivés
(thèmes disponibles, documents indexés proposés) étaient faux. Sans erreur ni état vide : une réponse
tronquée qui se faisait passer pour complète.

## Décision

`status` et `theme` deviennent des paramètres de requête optionnels de la route de liste, filtrant en
base (correspondance exacte), portés par `DocumentListParams(PaginationParams)` côté backend :

```
GET /v1/knowledge-bases/{kb_id}/documents?status=indexed&theme=produit&limit=100&cursor=…
```

`DocumentService.list_for_knowledge_base` accepte `status`/`theme` et les traduit en clauses `WHERE`
supplémentaires avant la pagination par curseur — aucun changement à `apply_cursor_pagination`/
`build_page`. Additif : sans ces paramètres, le comportement et la réponse sont inchangés.

Trois usages, trois solutions distinctes côté frontend (`frontend/src/api/documents.ts`), jamais en
reconstituant un sous-ensemble en tronquant une page :

| Usage | Besoin réel | Solution |
|---|---|---|
| `DocumentsPanel` — liste/gestion des documents | Parcourir tous les documents, page par page | Pagination réelle (`knowledgeBaseDocumentsInfiniteQueryOptions`, `useInfiniteQuery`, bouton « Charger plus ») ; filtre de thème conservé côté client sur les pages déjà chargées — l'incomplétude reste visible (bouton présent) plutôt que silencieuse |
| `PlaygroundPage` → `RetrievalSettings` — sélecteur de documents interrogeables | Proposer n'importe quel document `indexed`, pas seulement les 50 plus récents | Filtre serveur `status=indexed` + parcours de tous les curseurs (`indexedDocumentsSelectorQueryOptions`), borné à `MAX_SELECTOR_PAGES=10` pages de 100 (1000 documents) ; `truncated: true` au-delà, affiché à l'utilisateur (`playground.settings.documentsTruncated`) plutôt que passé sous silence |
| `UploadPanel` — détection de doublon par nom de fichier | Hors périmètre de cette ADR : angle mort déjà documenté et assumé dans le code (`duplicateReturned`, `UploadPanel.tsx`) — non touché |

Clés de cache : `documentKeys.lists(kbId)` est le préfixe d'invalidation commun aux trois formes de
requête (page unique, infinie, sélecteur) ; `documentKeys.list`/`infiniteList`/`indexedSelector` en
dérivent. Les deux littéraux `['knowledge-bases']` relevés par l'audit (`DocumentsPanel.tsx`,
`UploadPanel.tsx`) sont remplacés par `knowledgeBaseKeys.all`, déjà exporté par `api/knowledge-bases.ts`.

### Un paramètre `Query()` frère casse le dépliage d'un modèle de requête (FastAPI 0.140.7)

Constaté empiriquement, pas dans la documentation FastAPI : un paramètre `pagination:
Annotated[PaginationParams, Query()]` se déplie normalement en paramètres de requête individuels tant
qu'il est **seul**. Ajouter un second paramètre `Query()` à côté (`status: Annotated[..., Query()] =
None`) fait retomber le premier sur son nom littéral — la requête attend alors un unique paramètre
`?pagination=…`, et même l'appel sans aucun paramètre répond `422` (`"loc": ["query", "pagination"]`).
Reproduit hors du projet avec un `FastAPI` minimal (deux paramètres, un modèle Pydantic + un `str |
None`). Correctif : ne jamais poser de paramètre `Query()` à côté d'un modèle de requête déjà annoté —
sous-classer le modèle (`DocumentListParams(PaginationParams)`) et n'exposer qu'un seul paramètre
`Query()` par route.

## Conséquences

- Nouveaux paramètres additifs : aucune mutation de `/v1`, un client existant qui les ignore continue
  de fonctionner à l'identique.
- `indexedDocumentsSelectorQueryOptions` multiplie les requêtes HTTP par le nombre de documents
  `indexed` (une par tranche de 100) au lieu d'une seule — coût jugé acceptable : le Playground se
  charge une fois par visite, pas à chaque frappe.
- Tests : `backend/tests/services/test_documents.py::test_listing_documents_filters_by_status_and_theme`,
  `backend/tests/api/v1/test_document_listing_routes.py` (trois cas, dont la reproduction exacte de
  l'audit : un document au-delà de `limit` redevient atteignable par filtre) ;
  `frontend/src/features/knowledge-bases/DocumentsPanel.test.tsx` et
  `frontend/src/features/playground/PlaygroundPage.test.tsx` (deux pages simulées, document de la
  deuxième page vérifié visible/filtrable/sélectionnable).
