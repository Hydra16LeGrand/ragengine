"""Pipeline d'ingestion : precheck (layout + langues), profils de chunking, étapes.

Le precheck produit le rapport soumis à la validation humaine (Schéma A) ; les
traitements lourds ou GPU sont exécutés par les workers Celery, jamais dans le
process FastAPI (CONTRIBUTING.md §2).
"""
