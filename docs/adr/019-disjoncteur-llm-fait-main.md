# ADR-019 — Disjoncteur LLM fait main, posé une seule fois à la résolution

**Date :** 2026-07-30 · **Statut :** accepté

## Contexte

Phase 9 (résilience) : protéger les appelants (`RetrievalService.chat`, la génération
d'évaluation) d'un fournisseur LLM externe (Anthropic, OpenAI, Groq) en panne prolongée, sans
laisser chaque requête retenter un aller-retour réseau voué à l'échec pendant que le fournisseur
se rétablit.

## Décisions

**1. Fait main, pas de bibliothèque tierce.** Le besoin exact — trois états (`closed`/`open`/
`half_open`), un compteur d'échecs consécutifs, une horloge — tient en une trentaine de lignes
(`app/providers/llm/circuit_breaker.py`, `_Breaker`). `pybreaker` n'a pas d'API `async` native
(il faudrait l'envelopper dans un thread, ou accepter un faux positif de comptage sous
concurrence) ; `aiobreaker` ajoute une dépendance pour un besoin qui ne justifie pas d'en
apprendre les particularités. Aucune des deux n'aurait réduit le code de façon significative.

**2. Posé une seule fois, à `resolve_llm_provider` (`app/providers/llm/registry.py`), jamais
dans un adaptateur concret.** Les trois adaptateurs (`anthropic.py`/`openai.py`/`groq.py`)
partagent la même politique ; la dupliquer dans chacun créerait trois compteurs indépendants
pour un même fournisseur si deux implémentations du même vendeur coexistaient, et obligerait à
répéter la logique à chaque nouveau fournisseur (CONTRIBUTING.md §3 : un composant substituable a un
seul point de câblage). `CircuitBreakerLLMAdapter` enveloppe tout `LLMPort` sans changer le
port : les appelants (`RetrievalService.chat`) n'ont rien à savoir du disjoncteur.

**3. État en mémoire du process, par `provider_id` — pas de Redis partagé.** Un disjoncteur
protège l'aval d'*un* fournisseur en panne pendant la durée de vie d'*un* worker/process
FastAPI ; un état perdu au redémarrage n'est pas un défaut pour ce cas d'usage (contrairement à
un rate limiter, qui doit rester correct entre plusieurs process — ADR-018). `_BREAKERS` est un
simple `dict[str, _Breaker]` de module, créé paresseusement (`breaker_for`) ; la course entre
deux requêtes concurrentes sur un `provider_id` jamais vu ne peut créer, au pire, que deux
`_Breaker` dont un seul gagne la place — perte d'un objet fraîchement alloué, jamais une
incohérence d'état, sous le modèle de concurrence coopérative d'`asyncio`.

**4. Seuils configurables (`LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5`,
`LLM_CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS=30`), lus une fois à la création du disjoncteur d'un
`provider_id`.** Cohérent avec `Settings` figée pour la durée de vie du process (`get_settings`,
`lru_cache`) : un disjoncteur déjà ouvert ne change pas de seuil sous ses pieds.

**5. Un refus du modèle (`LLMRefusalError`) ne compte ni pour ni contre le disjoncteur.** Le
fournisseur a répondu — c'est une requête rejetée par son classificateur de sécurité, pas un
signal de panne de disponibilité (`app/providers/llm/base.py`, docstring de `LLMRefusalError`).
Le compter comme échec ouvrirait le circuit sur des requêtes légitimes sans rapport avec une
panne réelle.

**6. Un échec en cours de flux (`stream()`, après que des fragments ont déjà été émis) compte
quand même comme un échec.** Politique simple et assumée plutôt qu'une distinction
« échec immédiat » / « échec en cours de route » : un flux interrompu est un signal de panne au
moins aussi fort qu'un échec avant le premier fragment, et la distinction ajouterait une branche
pour un bénéfice marginal.

**7. Une sonde (`half_open`) qui échoue rouvre le circuit immédiatement**, sans attendre un
nouveau seuil d'échecs consécutifs — elle a déjà prouvé que le fournisseur n'est pas revenu.
`opened_at` est réarmé à chaque ouverture, y compris une réouverture immédiate après sonde, pour
redonner un plein délai avant la prochaine tentative.

## Conséquences

- Un fournisseur LLM en panne prolongée échoue vite (`LLMCircuitOpenError`, `503`) après le
  seuil, plutôt que de laisser chaque requête attendre un timeout réseau complet.
- L'état du disjoncteur ne survit pas à un redémarrage de process ni ne se partage entre workers
  — acceptable pour ce mécanisme de protection à court terme (cf. décision 3) ; à revisiter si un
  déploiement multi-worker constate un « split brain » gênant sur la décision d'ouverture (aucun
  fait vérifié en ce sens à ce jour).
- Ajouter un quatrième fournisseur LLM (`app/providers/llm/registry.py`, `_REGISTRY`) obtient le
  disjoncteur gratuitement — aucune modification de `circuit_breaker.py` n'est nécessaire.
