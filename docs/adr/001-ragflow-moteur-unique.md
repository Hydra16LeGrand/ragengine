# ADR-001 — RAGFlow comme moteur unique d'ingestion et de retrieval

**Date :** 2026-07-25 · **Statut :** accepté

## Contexte
Le brief initial prévoyait un vector store pluggable (Qdrant, PGVector, Chroma). Or RAGFlow embarque son propre pipeline (Deep Document Understanding, templates de chunking, recherche hybride dense + BM25) et son propre stockage (doc engine Elasticsearch ou Infinity) : brancher un vector store externe imposerait de contourner ou réimplémenter ses briques internes, au prix d'un système hybride et fragile.

## Décision
RAGFlow (Slim Docker) est **le moteur unique** d'ingestion, d'indexation et de retrieval. On l'utilise à fond, tel qu'il est conçu — aucun pipeline maison concurrent, aucun contournement de ses briques. Il reste encapsulé derrière `IngestionEnginePort` (adaptateur HTTP unique dans `providers/engine/ragflow/`) : le domaine ne le connaît pas.

## Conséquences
- Abandon du vector store pluggable ; le seul choix de stockage est le doc engine RAGFlow (Elasticsearch par défaut, Infinity en option de déploiement).
- La promesse de modularité (§2 du brief) porte désormais sur : modèle d'embedding, reranker, LLM de génération, observabilité.
- Le chunking adaptatif du precheck se traduit par la sélection de templates et paramètres natifs RAGFlow, pas par un chunker maison.
- La montée de version RAGFlow devient un événement d'architecture (audit de l'API à chaque upgrade).
