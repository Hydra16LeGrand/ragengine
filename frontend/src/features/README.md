# `src/features/` — modules métier

Un dossier par domaine fonctionnel. Chaque feature est autonome : ses écrans, ses composants
spécifiques, ses hooks de données (au-dessus de `src/api/`) et ses types y vivent ensemble.

Règles :

- une feature n'importe **jamais** l'intérieur d'une autre feature ; ce qui doit être partagé
  remonte dans `src/components/` (UI générique) ou `src/api/` (accès données) ;
- aucun `fetch` direct : tout passe par le client de `src/api/` ;
- aucune chaîne d'UI en dur : les libellés vivent dans `src/i18n/`.

Les dossiers sont vides en Phase 0 — c'est volontaire, ils se remplissent en Phases 6 et 7.
