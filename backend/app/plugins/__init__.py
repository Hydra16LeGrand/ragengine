"""Greffons du cœur, découverts par `app.core.plugins.get_plugins()`.

Chaque sous-paquet est un greffon : il expose un attribut `plugin`
(`app.core.plugins.Plugin`) et ne dépend que du cœur. Ce paquet ne contient
rien d'autre ; vide, l'application tourne avec le seul cœur (ADR-069).
"""
