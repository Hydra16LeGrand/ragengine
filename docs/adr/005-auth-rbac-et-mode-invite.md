# ADR-005 — Comptes et rôles dès la v1, avec mode invité sans connexion

**Date :** 2026-07-25 · **Statut :** accepté

## Contexte
Le modèle Premium futur exige des comptes et des rôles. Mais au lancement, un visiteur doit pouvoir tester le produit **sans se connecter**, et n'être invité à s'authentifier qu'au moment où il tente une action qui l'exige.

## Décision
- **RBAC dès la v1** : `User`, `Role` (au minimum `admin`, `member`), permissions par knowledge base. Clés API rattachées à un compte.
- **Mode invité** : sans connexion, un visiteur accède au Playground (sur les KBs marquées publiques/démo) et peut lancer une **ingestion d'essai** dans un espace éphémère soumis à quotas (taille, nombre de documents, TTL avec purge automatique).
- **Élévation progressive** : toute action persistante (créer/gérer une KB durable, obtenir une clé API, administrer) déclenche le parcours de connexion/inscription, sans perdre le contexte en cours.

## Conséquences
- La matrice permissions (invité / member / admin) est définie en Phase 1 avant tout endpoint.
- Le nettoyage des espaces invités est une tâche planifiée (Celery beat).
- L'UI doit traiter l'état « invité » comme un état de premier rang (bandeaux d'incitation, verrous visuels élégants), pas comme un cas d'erreur.
