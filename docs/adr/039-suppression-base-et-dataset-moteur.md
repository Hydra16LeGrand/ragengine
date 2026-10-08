# ADR-039 — Supprimer une base supprime aussi son dataset moteur, de façon synchrone

**Date :** 2026-08-16 · **Statut :** accepté · **Portée :** `KnowledgeBaseService.delete()`,
principe d'isolation du harnais (Phase 14, principe directeur n°6)

## Contexte

Constaté le 2026-08-15 : `KnowledgeBaseService.delete()` (`backend/app/services/knowledge_bases.py`)
ne faisait que `DELETE` de la ligne PostgreSQL — les `ON DELETE CASCADE` emportaient bien
appartenances, documents, jobs et journal, mais **aucun appel au moteur**. Le dataset RAGFlow
sous-jacent survivait indéfiniment : 71 bases accumulées en développement, et le harnais de la
Phase 14 devait compenser par un nettoyage manuel dans un `finally` qui ne survit pas à une
exécution interrompue. C'est aussi le prérequis à l'isolation stricte : une suite qui prétend
nettoyer sans le vérifier n'apporte qu'une promesse.

## Décision

**1. Supprimer le dataset côté moteur** à la suppression d'une base, via le port d'ingestion
existant (`IngestionEnginePort.delete_dataset`), jamais en important un SDK concret ailleurs
(ADR-001, CONTRIBUTING.md §3).

**2. Synchrone, pas de tâche Celery.** La suppression est déjà une opération utilisateur explicite
sur un chemin de requête ; l'appel moteur est un unique `DELETE` HTTP, rapide et sans GPU, et le
port est asynchrone et `await`é naturellement. Une tâche asynchrone ajouterait une pièce mobile
(file, reprise, ordonnancement, cohérence éventuelle) pour rien : elle créerait surtout une fenêtre
où la ligne a disparu mais pas le dataset si le worker est down — recréer la fuite comme problème de
consistance distribué. On suit l'existant : `DocumentService.delete()` supprime déjà son document
moteur de façon synchrone.

**3. Moteur injoignable : le transitoire aborte, le permanent est avalé.** Même contrat que
`DocumentService._forget_in_engine` : une erreur *transitoire* (`503`) propage et annule toute la
suppression — le moteur revient, et supprimer quand même créerait l'orphelin que la méthode existe
pour éviter. Une erreur *permanente* (`102`, « introuvable ou interdit » — ce que RAGFlow répond
pour un dataset déjà retiré) est journalisée et la suppression continue : rejouer ne réparerait
rien, et bloquer l'utilisateur sur une base déjà propre côté moteur serait une impasse. Une base
jamais versée (`ragflow_dataset_external_id is None`) ne touche pas au moteur, sans erreur.

**4. Ordre : moteur d'abord, ligne ensuite.** Son identifiant de dataset ne vit que dans la ligne
qu'on efface : échouer après le commit rendrait l'orphelin inatteignable ; échouer avant ne coûte
qu'une suppression à rejouer.

## Conséquences

- **Changement de comportement produit, assumé** : supprimer une base devient réellement
  destructif côté moteur (c'était le contrat implicite, il devient effectif). À dire aux
  utilisateurs, pas à glisser.
- Le harnais retire son nettoyage moteur manuel (`RagflowObserver.delete_dataset` dans le
  `finally`) : il vérifie désormais que la suppression applicative a bien nettoyé le dataset.
- `EngineTransientError` peut désormais remonter du `DELETE /knowledge-bases/{id}` (`503`) — cas
  nouveau pour ce endpoint, contractuellement propre (RFC 9457, ADR-007).
