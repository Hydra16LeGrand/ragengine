# ADR-006 — Licences : aucune contrainte commerciale pour la v1

**Date :** 2026-07-25 · **Statut :** accepté

## Contexte
La v1 est un déploiement souverain local sans distribution commerciale. Les briques pressenties : RAGFlow (Apache 2.0), BGE-M3 et bge-reranker-v2-m3 (MIT), Elasticsearch (Elastic License v2 / SSPL selon distribution), Langfuse (MIT pour le cœur).

## Décision
Aucune contrainte de licence bloquante pour la v1 ; pas de filtrage des briques sur ce critère aujourd'hui.

## Conséquences
- Point de vigilance à **réévaluer obligatoirement** avant tout lancement Premium/commercial (notamment Elasticsearch et toute brique OCR ajoutée en cours de route type MinerU/AGPL).
- Chaque nouvelle dépendance d'extraction notera sa licence dans la PR qui l'introduit.
