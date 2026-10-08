# ADR-003 — Multilingue par conception et détection des langues au precheck

**Date :** 2026-07-25 · **Statut :** accepté

## Contexte
Le corpus sera principalement francophone (documents administratifs), mais le système doit supporter d'autres langues, et il est utile de savoir quelles langues contient chaque document.

## Décision
- Modèles locaux **multilingues par défaut** : embedding **BGE-M3**, reranker **bge-reranker-v2-m3** (validés pour le français, couvrent ~100 langues). OCR multilingue.
- Le **precheck détecte les langues de chaque document** ; le résultat figure dans le rapport de precheck (affiché à l'utilisateur), dans les métadonnées du document et comme filtre potentiel au retrieval.

## Conséquences
- Pas de modèle mono-langue français en v1 ; un provider d'embedding spécialisé pourra s'ajouter plus tard via `EmbeddingPort` sans refonte.
- La détection de langue est une étape CPU légère du precheck (ex. bibliothèque type fastText/lingua), pas une tâche GPU.
- Les jeux d'évaluation (Phase 8) incluront des requêtes en français ET dans au moins une autre langue.
