# ADR-071 — Chemins d'essai : démonstration sans RAGFlow ni GPU, et pile réelle sans GPU

**Date :** 2026-10-04 · **Statut :** accepté · **Prolonge :** ADR-070 (adaptateur factice), ADR-051 (TEI)

## Contexte

Un relecteur externe a relevé que le cœur publié n'offre aucun chemin d'essai sans GPU : quatre
fichiers Compose à lancer dans l'ordre, une amorce manuelle de RAGFlow (recopier une clé dans
`.env`), un second `docker compose` sans lequel le worker `default` ne tourne pas (l'ingestion
reste en `queued`), et une image TEI liée aux GPU Ada (`89-1.9`).

## Décision

1. **Démonstration : `scripts/quickstart.sh demo`** — PostgreSQL, Redis, API, worker `default`,
   Traefik et frontend, avec `INGESTION_ENGINE=fake-shared`. Aucune recherche sémantique, aucun OCR
   (texte UTF-8 seulement) : la documentation le dit en tête.
2. **Partage d'état du factice : Redis.** L'état de `FakeIngestionEngine` est propre au process
   (limite posée par ADR-070) ; avec l'API et le worker dans deux conteneurs, le document indexé par
   le worker est invisible à la recherche servie par l'API. Nouveau moteur `fake-shared`
   (`SharedStateFakeEngine`) : chaque opération du port prend un verrou Redis, recharge l'état
   (JSON, une clé), s'exécute, le réécrit. Les règles du factice ne sont pas redéfinies — un point
   d'extension `_operation` du parent suffit.
3. **Alternatives écartées.** *Celery eager dans l'API* : une tâche exécutée dans une requête
   asynchrone appelle `asyncio.run` dans une boucle active (`app.workers.session`), le mode eager
   ignore les `countdown` des sondages, et la topologie ne ressemblerait plus à la production.
   *État en PostgreSQL* : une table, donc une migration du schéma de production pour un
   mode de démonstration. Redis est déjà là (broker), la topologie reste celle de la production
   (API + worker), et aucun code de production n'est touché.
4. **Garde-fous.** `fake-shared` est refusé en staging et en production comme `fake`
   (`Settings`), annoncé `is_fake` par `GET /api/v1/providers`. Valeur par défaut inchangée :
   `ragflow`.
5. **Pile réelle sans GPU : `docker/docker-compose.cpu.yml`**, un fichier à part qui remplace
   `docker-compose.gpu.yml` (mêmes services, conteneurs, ports et volumes, image
   `text-embeddings-inference:cpu-1.9`, tag réglable par `RAGENGINE_TEI_CPU_TAG`, pas de réservation
   GPU, pas de `worker-gpu` : aucune tâche n'est routée sur `gpu`, ADR-013). Un fichier séparé
   plutôt qu'un override ou un profil : retirer la réservation `deploy.resources` d'un fichier
   fusionné exige `!reset`, et un profil changerait le fichier GPU en service. Ici, le fichier
   GPU n'est pas modifié.
6. **Une commande, trois chemins** : `scripts/quickstart.sh demo|cpu|gpu`. Pour `cpu` et `gpu` :
   cœur avec workers, inférence, RAGFlow, attente `healthy`, amorce de RAGFlow avec écriture de la
   clé émise dans `.env` (jamais affichée, comme `start-prod.sh`), recréation de l'API et des
   workers, vérification finale (santé, interface, `/embed` et `/rerank` de TEI).

## Vérifié

- Test automatisé `backend/tests/test_demo_mode.py` : l'API (ASGI) et **un vrai worker Celery
  dans un autre process**, broker Redis et PostgreSQL réels, aucune tâche appelée à la main. Avec
  `fake-shared`, l'ingestion atteint `completed` et la recherche retrouve le texte indexé par le
  worker. Avec `fake`, la même recherche ne retrouve rien : la contre-épreuve. Vérifié en
  sabotant le rechargement de l'état : le test échoue.
- Configuration de production inchangée : `docker compose config` des cinq fichiers (et des
  profils `workers`, `tunnel`), avec `.env.example` puis `.env.prod`, est identique avant et après,
  octet pour octet.
- TEI v1.9.0 : le tag `cpu-1.9` existe (`docker manifest inspect`) ; la branche CPU charge
  XLM-RoBERTa (`Config::XlmRoberta` × `Device::Cpu`, `backends/candle/src/lib.rs`), architecture de
  BGE-M3 comme de bge-reranker-v2-m3 ; même `Dockerfile` que le GPU (`PORT=80`, cache `/data`, `curl`).

## Non vérifié

- **La variante CPU n'a pas été exécutée** : aucun conteneur démarré (mémoire insuffisante sur la
  machine de développement). Ni latence, ni empreinte mémoire, ni parcours bout en bout sur
  matériel sans GPU. Les besoins en RAM annoncés (16 Go conseillés) sont une estimation à partir
  du plafond d'Elasticsearch (4 Go) et du poids des modèles (~4,4 Gio), pas une mesure.
- `quickstart.sh` n'a été vérifié que par `bash -n` (shellcheck absent) et relecture ; aucun de
  ses trois chemins n'a été lancé.

## Conséquences

- L'API et le frontend de la démonstration partagent le projet Compose `ragengine-demo` et le
  fichier `.env.demo` (ignoré par git) ; elle cohabite avec le développement si les ports hôtes
  (5433, 6380, 8000, 8090) sont libres.
- Un utilisateur du chemin `cpu` ne doit pas lancer `docker-compose.gpu.yml` en plus.
