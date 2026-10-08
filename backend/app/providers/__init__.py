"""Ports & adaptateurs des briques substituables (architecture hexagonale).

Chaque sous-package déclare son interface dans `base.py` (ABC/Protocol) et ses
implémentations concrètes à côté, résolues par une factory pilotée par la
configuration. Les adaptateurs sont les seuls modules autorisés à importer un
SDK tiers (CONTRIBUTING.md §3.1).

Il n'existe volontairement pas de package `vector_stores/` : le stockage hybride
dense + BM25 est délégué au moteur RAGFlow (ADR-001).
"""
