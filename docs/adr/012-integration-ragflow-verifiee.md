# ADR-012 — Intégration RAGFlow : décisions issues d'un audit empirique réel

**Date :** 2026-07-26 · **Statut :** accepté

## Contexte
ADR-001 différait délibérément la conception d'`IngestionEnginePort` jusqu'à ce que l'API réelle de RAGFlow soit auditée (Phase 4), pour ne pas figer une interface sur des suppositions. L'audit a été mené contre une instance RAGFlow v0.26.4 réellement déployée, avec notre propre service Xinference comme fournisseur d'embedding/rerank — pas sur la seule documentation. Le rapport complet est résumé dans `docs/ingestion-engine-architecture.md` ; cette ADR consigne les décisions qui en découlent.

## Décisions

**1. `IngestionEnginePort` n'expose pas d'état fin garanti.** RAGFlow ne porte qu'un seul champ d'état discret (`run`) ; chunking/embedding/indexing n'existent que comme texte libre non contractuel dans un journal de progression. Le port projette ces phases par heuristique (recherche de marqueurs textuels observés empiriquement), explicitement documentée comme best-effort (`EngineParseStatus.inferred_phase: JobState | None`). Aucun code appelant ne doit supposer une précision que RAGFlow ne fournit pas.

**2. RAGEngine porte sa propre veille anti-blocage (watchdog).** Un bug RAGFlow constaté (pas supposé) laisse un document bloqué indéfiniment en `RUNNING` sans jamais atteindre un état terminal, sur une simple erreur de parsing en amont de la création de ses tâches internes. Aucun mécanisme RAGFlow ne s'en remet seul. Le polling Celery (Phase 3, adapté) porte donc un délai de garde mesuré depuis la dernière évolution observée, au-delà duquel RAGEngine annule lui-même côté RAGFlow (`documents/stop`, vérifié fonctionnel) et fait échouer le job.

**3. Les datasets RAGFlow sont nommés par UUID de `KnowledgeBase`, jamais par nom humain.** Un nom dupliqué n'est pas une erreur côté RAGFlow : il est renommé en silence. Nommer par UUID rend la création idempotente sans vérification préalable, et élimine une classe entière de doublons silencieux après une panne réseau.

**4. Le service de retrieval (Phase 5) doit piloter `similarity_threshold` conjointement à l'activation du reranking.** Le score combiné change d'échelle quand un reranker est utilisé (le score de reranking remplace la similarité vectorielle dans la formule) et peut tomber sous le seuil par défaut, vidant les résultats sans erreur. `similarity_threshold` n'a donc pas de valeur par défaut cachée dans l'adaptateur : c'est un paramètre explicite du port, à la charge de l'appelant.

**5. Les erreurs RAGFlow sont lues dans le corps (`code`), jamais dans le seul statut HTTP.** Le comportement observé (HTTP 200 quasi systématique, y compris sur échec métier) rend `raise_for_status()` inopérant. L'adaptateur traduit `code` en hiérarchie `EngineError`, avec une distinction transitoire/permanente pour piloter le retry Celery (seules les erreurs transitoires — connexion, exécution en cours, exception serveur générique — sont éligibles).

**6. Déploiement : tag de release épinglé, jamais `main` ; réseau isolé avec relais TCP vers Xinference.** Deux pièges réels rencontrés en audit (documentés en détail dans `docs/ingestion-engine-architecture.md` §6) : `main` bind-monte un script d'entrée incompatible avec l'image publiée correspondante ; fusionner le réseau Docker de RAGFlow avec celui de RAGEngine crée une collision de nommage sur `redis` qui fait échouer RAGFlow silencieusement.

## Conséquences
- `chunking`, `embedding`, `indexing` restent des états RAGEngine à part entière (cohérent avec `docs/domain-model.md` §3, qui les avait déjà qualifiés de « notre abstraction ») — leur projection depuis RAGFlow est reconnue fragile et documentée comme telle, pas cachée.
- `IngestionJob.progress_percent` reste non alimenté par le `progress` brut de RAGFlow (flottant `[-1,1]`, bruité) : la Phase 4 n'introduit pas de fausse précision dans l'interface.
- Toute évolution future de RAGFlow qui exposerait un état plus fin n'imposera qu'une évolution de l'adaptateur, jamais du port.
