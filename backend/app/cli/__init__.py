"""Commandes d'exploitation exécutées à la main, hors requête HTTP.

Distinct de `scripts/` (racine du dépôt, outillage de mesure/recherche) : ce
paquet vit dans `app` pour réutiliser directement `Settings`, le moteur async
et les modèles — pensé pour tourner **dans** le conteneur `api` déjà déployé
(`docker exec ragengine-api python -m app.cli.<commande>`), jamais depuis
l'hôte (`POSTGRES_HOST=postgres` n'y est résolvable que sur le réseau Docker).
"""
