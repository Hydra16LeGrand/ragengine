# ADR-013 — Le pipeline d'ingestion RAGFlow n'a plus besoin de la file `gpu`

**Date :** 2026-07-27 · **Statut :** accepté

## Contexte
La Phase 3 a assigné `run_precheck` et `run_embedding` à la file Celery `gpu` (`concurrency=1`), en anticipant que « ces deux étapes toucheront le service d'inférence GPU à partir de la Phase 4 » (`app/workers/celery_app.py`, commentaire d'origine). L'audit et l'implémentation de la Phase 4 ont établi une réalité différente :

1. **Le precheck de RAGEngine (`app/ingestion/precheck/`) est délibérément sans dépendance GPU** — analyse légère (extraction de texte, détection heuristique de tableaux, détection de langue), quelques secondes, aucun modèle chargé.
2. **RAGFlow effectue lui-même l'embedding et le rerank en interne**, en appelant notre service Xinference de son propre côté (`docs/ingestion-engine-architecture.md`, ADR-012). Les tâches Celery de RAGEngine (`run_chunking`, `run_embedding`, `run_indexing` au sens de la Phase 3) ne font donc, dans leur réalité Phase 4, que des **appels HTTP vers l'API RAGFlow et du sondage** (`ensure_dataset`, `upload_document`, `start_parsing`, `get_parse_status` en boucle) — aucun travail GPU n'a lieu dans le process qui exécute ces tâches.
3. La sérialisation qui protège réellement la VRAM (ADR-011, fait vérifié empiriquement : verrou interne du `ModelActor` Xinference, 4 requêtes simultanées = 0,98× le temps séquentiel) s'applique **à tout appelant** — RAGFlow y compris — indépendamment de la file Celery sur laquelle tourne le code qui déclenche l'appel.

## Décision
Les quatre tâches du pipeline d'ingestion (`run_precheck`, `run_chunking`, `run_embedding`, `run_indexing`) routent toutes vers la file **`default`**, pas `gpu`. Aucune ne fait de travail GPU dans le process worker ; les mettre sur `gpu` gaspillerait l'unique créneau de concurrence 1 sur du travail réseau/CPU, ralentissant sans raison toute future tâche qui, elle, en aurait vraiment besoin.

La file `gpu` (`concurrency=1`, ADR-002) reste réservée aux appels **directs** de RAGEngine à `EmbeddingPort`/`RerankerPort` — un chemin qui n'existe pas dans le pipeline d'ingestion actuel (RAGFlow s'en charge), mais qui pourrait apparaître en Phase 5 (reranking local optionnel sur des résultats de recherche, plan interne Phase 5) ou dans un futur mode d'ingestion sans RAGFlow.

## Conséquences
- `app/workers/celery_app.py` (`TASK_ROUTES`) est corrigé : les quatre tâches d'ingestion passent en file `default`.
- Le commentaire de Phase 3 qui justifiait l'ancienne assignation est obsolète et corrigé en même temps, pour ne pas induire en erreur une future lecture du code.
- Le worker `worker-gpu` (`docker-compose.gpu.yml`) n'est plus sollicité par l'ingestion : il reste up, prêt pour la Phase 5, mais son utilité pour la Phase 4 telle qu'implémentée est nulle — ce n'est pas un défaut, c'est la conséquence normale de déléguer l'ingestion à un moteur qui gère lui-même son propre GPU.
