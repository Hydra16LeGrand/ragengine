# ADR-002 — Matériel confirmé et service d'inférence GPU dédié

**Date :** 2026-07-25 · **Statut :** accepté

## Contexte
Le GPU est une RTX 4080 **Laptop** : 12 Go de VRAM confirmés (et non 16 Go comme la version desktop). RAM système : 32 Go. La génération LLM est intégralement externalisée vers des APIs tierces. RAGFlow Slim n'embarque aucun modèle : il consomme des fournisseurs d'embedding/rerank externes.

## Décision
Les modèles locaux (embedding + reranker) vivent dans un **service d'inférence GPU dédié et unique** (Xinference ou TEI, à trancher en Phase 3), consommé à la fois par RAGFlow et par la façade FastAPI. Ce service est le **sémaphore VRAM** : concurrence et batch bornés par configuration, chargement singleton, warm-up et mesure de l'empreinte VRAM au démarrage. Les traitements lourds pilotés par RAGEngine (precheck, OCR) passent par la queue Celery `gpu` (`concurrency=1`).

## Conséquences
- Budget VRAM co-résident (OCR + embedding + reranker) plafonné à **10 Go** (marge de 2 Go).
- Tout ajout de modèle local documente son empreinte VRAM estimée.
- Aucun LLM génératif local, jamais.
- Deux ingestions simultanées se sérialisent naturellement ; l'OOM est impossible par construction, pas par vigilance.
