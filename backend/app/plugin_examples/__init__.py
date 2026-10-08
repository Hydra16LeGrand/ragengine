"""Greffons d'exemple — jamais découverts, donc jamais actifs.

Le cœur ne scanne que `app.plugins` (`app/core/plugins.py`). Ce paquet-ci est hors de son chemin :
rien de ce qu'il contient ne s'active tout seul. Pour en activer un, copier son dossier sous
`app/plugins/` (voir `docs/extending.md`) ; un test (`tests/test_plugin_example.py`) prouve qu'il
s'enregistre et sert sa route.
"""
