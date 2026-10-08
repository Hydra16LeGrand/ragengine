# ADR-045 — Filtrage du retrieval par métadonnées existantes, en pré-résolution

**Date :** 2026-08-30 · **Statut :** accepté

## Contexte

`RetrievalFilters` n'exposait que `knowledge_base_id`, `top_k`, `rerank`, `similarity_threshold` et
`document_ids` — aucun filtrage par langue, type de document ou date, alors que ces métadonnées
existent déjà en base (`Document.detected_languages`, `.content_type`, `.created_at`, ADR-003).
Décision utilisateur du 2026-08-30 : implémenter ce filtre plutôt que d'introduire une capacité de
métadonnées personnalisées à l'ingestion (aucun schéma d'entrée n'existe à
`POST .../documents/ingest`, ADR-014 — délibéré, hors périmètre de cette décision).

## Décision

**Filtrage en pré-résolution côté RAGEngine, jamais côté moteur.** `IngestionEnginePort.search`
n'accepte qu'un sous-ensemble de documents déjà résolu (`document_refs`, filtre natif RAGFlow
vérifié) — aucune notion de métadonnées arbitraires côté moteur. `RetrievalService._resolve_document_refs`
traduit donc `languages`/`content_types`/`ingested_after`/`ingested_before` en une requête
PostgreSQL sur `documents`, combinée en ET avec `document_ids` s'il est fourni, puis rend la liste de
`EngineDocumentRef` déjà utilisée pour `document_ids` seul. Aucun `WHERE` post-hoc sur des résultats
déjà rendus par le moteur (principe déjà posé en tête du module, ADR-001).

**Deux sémantiques d'échec distinctes, assumées.** `document_ids` cite des objets précis : un
identifiant absent de la base reste une erreur (422, `_require_documents_exist`, inchangé). Les
nouveaux filtres sont **descriptifs** : une combinaison qui n'exclut aucun document ne lève jamais
d'erreur, elle rend une liste vide — exactement le comportement déjà établi pour une base jamais
ingérée. Une langue filtrée qui ne correspond à aucun document de la base est une réponse valide, pas
une faute de l'appelant.

**Champs retenus, rien d'inventé.** `languages` (recoupe `detected_languages`, opérateur `&&`),
`content_types` (`IN` sur `content_type`), `ingested_after`/`ingested_before` (bornes sur
`created_at`). Pas de filtre par « section » : aucune métadonnée de section n'est stockée à ce niveau
de granularité (les fragments vivent dans RAGFlow, pas en PostgreSQL) — hors périmètre de cette
décision, pas un oubli.

## Conséquences

- `backend/app/domain/schemas/retrieval.py` : quatre nouveaux champs sur `RetrievalFilters`, hérités
  par `RetrievalSearchRequest` et `RetrievalChatRequest`.
- `backend/app/services/retrieval.py` : `_resolve_document_refs` prend désormais `RetrievalFilters`
  entier plutôt qu'une liste d'identifiants ; l'existence de `document_ids` est vérifiée séparément
  par `_require_documents_exist` pour ne pas mélanger les deux sémantiques d'échec.
- Quatre tests ajoutés (`backend/tests/services/test_retrieval.py`) : filtrage par langue et par
  type, absence de correspondance sans erreur, filtrage par date. Prouvé aussi contre la stack réelle
  (compte et base jetables, deux documents de langues différentes, filtre `languages=['fr']` excluant
  le document anglais même sur une requête portant sur son propre contenu lexical).
- `docs/api-guide.md` §4 documente les quatre paramètres.
- **Non fait, hors mandat** : aucune UI Playground pour ces filtres (ajout trivial différé, pas
  requis par la décision) ; aucune métadonnée personnalisée à l'ingestion (cf. Contexte).
