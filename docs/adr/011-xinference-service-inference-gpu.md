# ADR-011 — Xinference comme service d'inférence GPU

**Date :** 2026-07-26 · **Statut :** accepté

## Contexte
CONTRIBUTING.md §2/§3 et ADR-002 imposent un **service d'inférence GPU dédié unique**, hébergeant à la fois l'embedding (BGE-M3) et le reranking (bge-reranker-v2-m3), consommé aussi bien par la façade (`EmbeddingPort`/`RerankerPort`, Phase 2) que par RAGFlow (Phase 4). Le choix restait ouvert entre Xinference et TEI (Text Embeddings Inference).

## Décision
**Xinference.** TEI sert un seul modèle par instance : héberger BGE-M3 et bge-reranker-v2-m3 imposerait deux conteneurs, donc deux budgets VRAM à coordonner séparément — contraire à l'exigence d'un service unique. Xinference sert plusieurs modèles dans un seul processus, avec une API compatible OpenAI pour les embeddings.

**La limite anti-OOM réelle vit dans le service d'inférence, pas seulement dans le worker Celery.** Le `concurrency=1` du worker `gpu` (ADR-002, CONTRIBUTING.md §2) ne sérialise que les appels déclenchés par nos propres tâches ; à partir de la Phase 4, RAGFlow appellera aussi Xinference directement pour ses propres besoins d'embedding, en dehors de notre file Celery. Le budget VRAM n'est donc garanti que si Xinference borne lui-même sa concurrence de traitement — c'est ce point qui doit être vérifié empiriquement (nombre de workers/replicas du moteur de modèle), pas supposé.

## Conséquences
- `docker-compose.gpu.yml` déploie un unique service Xinference, runtime NVIDIA, hébergeant les deux modèles.
- Les adaptateurs `LocalEmbeddingAdapter`/`LocalRerankerAdapter` (Phase 2) avaient anticipé un contrat HTTP par hypothèse (OpenAI-compatible pour l'embedding, Cohere/Jina-compatible pour le rerank) faute de service réel à interroger. La Phase 3 vérifie ce contrat contre une instance Xinference réellement démarrée sur le GPU de développement (RTX 4080 Laptop, 12 Go — confirmé présent) et corrige les adaptateurs si l'API réelle diverge.
- L'empreinte VRAM des deux modèles co-résidents est mesurée réellement (`nvidia-smi`), pas estimée, et consignée pour vérifier la marge de CONTRIBUTING.md §2 (< 10 Go).
