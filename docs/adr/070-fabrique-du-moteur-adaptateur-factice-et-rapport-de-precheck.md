# ADR-070 — Fabrique du moteur d'ingestion, adaptateur factice et rapport de precheck typé

**Date :** 2026-10-04 · **Statut :** accepté

## Contexte

Un relecteur externe a jugé le cœur publié du point de vue d'un développeur qui veut construire
dessus : les ports existent, l'extensibilité reste théorique. Trois constats : le moteur
d'ingestion n'était pas remplaçable (`resolve_ingestion_engine` renvoyait toujours l'adaptateur
RAGFlow, et l'audit de dette comptait sept doubles partiels de `IngestionEnginePort` dans les
tests, aucun adaptateur factice complet) ; `DocumentRead.precheck_report` était un dictionnaire
libre, donc sans contrat ; les points d'extension (fournisseurs, greffons) n'avaient ni exemple
ni page d'explication. Le déploiement hébergé en production ne doit pas changer de comportement.

## Décision

1. **Fabrique du moteur** (`providers/engine/registry.py`), sur le patron des registres LLM,
   embeddings et rerankers : un `Mapping` construit à l'import, piloté par `INGESTION_ENGINE`
   (`ragflow` par défaut). RAGFlow reste la seule implémentation de production (ADR-001).
2. **Adaptateur factice complet** (`providers/engine/fake.py`) : en mémoire, déterministe, il
   respecte tout le port (chaque méthode, chaque état de parsing, chaque erreur). Il est le double
   unique des tests — options de scénarisation (`statuses`, `failures`, `search_results`,
   `chunks`, `strict`…) plutôt que nouveaux doubles — et l'exemple de référence d'un adaptateur.
   Avec `INGESTION_ENGINE=fake`, l'API tourne sans RAGFlow ni GPU, jusqu'à `completed` puis une
   recherche qui retrouve le texte déposé (test de bout en bout). Sa recherche est lexicale.
3. **Sa limite, posée dans le code.** Ce n'est pas un moteur : aucune recherche sémantique, aucun
   OCR, aucune persistance, état propre au process (l'API et un worker lancés à part ne le
   partagent pas). CONTRIBUTING.md §3 interdit un pipeline concurrent de RAGFlow ; `Settings` refuse donc
   `INGESTION_ENGINE=fake` en staging et en production, comme ses autres garde-fous, et
   `GET /api/v1/providers` l'annonce (`engine.is_fake`).
4. **Rapport de precheck typé.** `PrecheckReport` est déplacé dans le domaine
   (`domain/schemas/precheck.py`, ré-exporté par `ingestion/precheck/schemas.py`) et
   `DocumentRead.precheck_report` devient `PrecheckReportRead | None`, publié dans l'OpenAPI.
   `PrecheckReportRead` valide comme `PrecheckReport` mais **restitue exactement les clés relues,
   dans leur ordre** : un rapport ancien (sans `two_column_page_count`, ajouté par la suite) ne
   reçoit aucune valeur par défaut. Le producteur continue d'écrire le modèle complet.
5. **Découverte des fournisseurs : registre codé en dur, pas d'`entry_points`.** Une poignée de
   fournisseurs et de moteurs ne justifie ni un mécanisme de découverte dynamique (ordre de
   chargement, paquets tiers qui s'enregistrent en silence, surface de sécurité) ni sa maintenance :
   la liste de ce qu'un déploiement peut exécuter reste lisible et testable en un endroit. Une
   page de documentation (`docs/extending.md`) et deux exemples réels (l'adaptateur factice, un
   greffon) suffisent. À rouvrir si des fournisseurs tiers distribués séparément deviennent un
   besoin réel. Seuls les greffons, des dossiers entiers ajoutés ou retirés, sont découverts
   (ADR-069).
6. **Greffon d'exemple** `app/plugin_examples/ping` : une route et un réglage par défaut, hors de
   `app/plugins/` — le seul paquet scanné — donc inactif ; le copier sous `app/plugins/` l'active.

## Conséquences

- Aucun changement pour le déploiement hébergé : `INGESTION_ENGINE` vaut `ragflow` sans variable posée, le
  JSON des rapports de precheck est inchangé octet pour octet (preuve : les 326 rapports de la base
  de dev relus contre le schéma et resérialisés à l'identique ; test de contrat), et le catalogue
  `GET /api/v1/providers` gagne seulement le champ `engine` (ajout, pas de mutation).
- Les doubles de test du port sont remplacés par l'adaptateur factice (17 classes retirées) :
  une évolution du port se répercute à un seul endroit.
- L'OpenAPI et `frontend/src/api/schema.ts` exposent `PrecheckReportRead` ; les champs à valeur par
  défaut y sont optionnels, car un rapport ancien ne les porte pas.
- Le futur chemin d'essai léger (sans RAGFlow ni GPU) a sa fondation ; il reste à lui donner un
  état partagé entre processus avant tout usage hors tests et démonstrations.
