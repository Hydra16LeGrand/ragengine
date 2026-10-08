# ADR-057 — TEI et worker GPU dédiés par environnement

**Date :** 2026-09-14 · **Statut :** accepté · **Précise :** ADR-051 (TEI) · **Révise :** Phase B de
le plan de déploiement interne (2026-09-06, « inférence GPU partagée entre dev et prod »)

## Résumé

- **Décision** : chaque environnement (dev, prod) a désormais son propre couple TEI
  (`inference-embedding`/`inference-reranker`) et son propre `worker-gpu`, sur son propre réseau Docker
  — plus de partage entre dev et prod, contrairement à ce que Phase B avait décidé.
- **Pourquoi** : Phase B partageait l'inférence GPU pour rester sous le plafond VRAM, mais laissait un
  point ouvert (un `worker-gpu` prod devrait rejoindre le réseau dev). `docker-compose.gpu.yml` avait
  aussi un vrai bug : `container_name` et le réseau externe étaient figés en dur, non paramétrés par
  `RAGENGINE_PROJECT_NAME` — une instance prod aurait collisionné avec dev au lieu d'en être isolée.
- **Pourquoi c'est sûr** : dev et prod ne tournent jamais en même temps sur cette machine (2 Go de RAM
  libres sur 29) — jamais deux jeux de modèles GPU chargés simultanément, donc pas de dépassement VRAM.
- Le cache de poids Hugging Face (volumes) reste **partagé**, délibérément non paramétré : poids publics
  re-téléchargeables, pas des données produit.

## Contexte

Phase B (2026-09-06) avait tranché pour un service d'inférence GPU **partagé** entre dev et prod, par
contrainte VRAM (CONTRIBUTING.md §2, marge de 10 Go) : un seul couple de conteneurs TEI, `INFERENCE_BASE_URL`
pointé dessus par les deux environnements. Cette décision laissait un point ouvert non résolu : un futur
`worker-gpu` prod devrait rejoindre le réseau Docker de **dev** (comme `ragflow-cpu` le fait déjà pour
ses propres dépendances, `docker-compose.ragflow.yml` §2) pour atteindre `inference-embedding`/
`inference-reranker` par leur nom de service — un couplage fragile entre les deux environnements, à
résoudre avant la première ingestion réelle en prod.

`docker-compose.gpu.yml` portait par ailleurs un vrai bug empêchant cette approche de fonctionner tel
quel : ses `container_name` et le nom du réseau `ragengine` externe qu'il consomme étaient figés en dur,
non paramétrés par `RAGENGINE_PROJECT_NAME` (contrairement à `docker-compose.yml` et
`docker-compose.ragflow.yml`, déjà corrects). Une instance prod aurait cherché le réseau `ragengine` de
**dev** et y aurait attaché ses propres conteneurs GPU — collision, pas isolation.

## Décision

Chaque environnement (dev, prod) reçoit désormais son propre couple TEI (`inference-embedding`,
`inference-reranker`) et son propre `worker-gpu`, sur son propre réseau Docker, entièrement paramétrés
par `RAGENGINE_PROJECT_NAME` (`docker-compose.gpu.yml` : `container_name`, réseau externe consommé,
`env_file` suivant `RAGENGINE_ENV_FILE` — même patron que le reste de la stack). Le cache de poids
Hugging Face (volumes `ragengine_inference_embedding_data`/`ragengine_inference_reranker_data`) reste
**partagé**, lui, délibérément non paramétré : ce sont des poids de modèles publics re-téléchargeables
(BGE-M3, bge-reranker-v2-m3, ~2,2 Gio), pas des données produit — les partager évite un re-téléchargement
inutile en prod et ne compromet aucune isolation qui compte.

## Pourquoi c'est sûr maintenant

La contrainte matérielle réelle de cette machine (2 Go de RAM libres sur 29, le plan de déploiement interne)
impose que dev et prod ne tournent **jamais** simultanément — donc jamais deux jeux de modèles GPU
chargés en même temps non plus. L'empreinte VRAM d'un seul environnement reste ~2,7 Gio (mesure
ADR-051), largement sous le plafond de 10 Go de CONTRIBUTING.md §2 : dupliquer les conteneurs (pas leur usage
simultané) ne consomme rien de plus que ce qu'un seul environnement consommait déjà.

## Conséquences

- Le point ouvert de Phase B est résolu : plus besoin qu'un `worker-gpu` prod rejoigne le réseau dev.
- Si dev et prod doivent un jour tourner concurremment sur du matériel plus généreux, l'isolation réseau
  est déjà structurellement en place — aucune migration de topologie à refaire à ce moment-là.
- `.env.prod` n'a plus besoin de `INFERENCE_BASE_URL` (variable de toute façon morte depuis ADR-051,
  remplacée par `EMBEDDING_INFERENCE_BASE_URL`/`RERANKER_INFERENCE_BASE_URL`) : les défauts de
  `Settings` suffisent, isolés par environnement une fois `docker-compose.gpu.yml` corrigé.
