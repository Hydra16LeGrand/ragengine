# ADR-068 — Rejeu du téléversement RAGFlow corrompu, dans l'adaptateur

**Date :** 2026-09-21 · **Statut :** accepté · **Ferme la correction du 2026-09-13 sur ADR-044 §5.1**

## Résumé

- **Le défaut** : RAGFlow sert son API par le serveur de développement Werkzeug (`app.run()`),
  qui corrompt parfois le corps d'un envoi multipart sous requêtes concurrentes (2 octets `\r\n`
  préfixés, mesuré ~37,5 % par tentative). La correction du 2026-09-13 avait établi que **rien ne
  rejoue ce défaut en production** : `_poll` code en dur `retryable=False`, seul le harnais de
  mesure rejouait.
- **La décision** : le rejeu vit dans `RagflowEngineAdapter.upload_document`
  (`backend/app/providers/engine/ragflow/client.py`) — jamais dans le worker ni le domaine. Après
  chaque upload, la taille enregistrée côté moteur est relue et comparée à la taille envoyée ; un
  écart (ou une panne pendant cette relecture) supprime le document et retéléverse, borné à 4
  tentatives, attente croissante 1/2/4 s, chaque rejeu journalisé.
- **Idempotence vérifiée, pas supposée** : RAGFlow ne déduplique jamais un document par lui-même
  (même défaut que le nom de dataset) — chaque `POST` réussi, corrompu ou non, crée une entrée
  distincte. D'où la suppression explicite (`DELETE`, best-effort) avant tout nouvel essai.
- **`_poll`/`retryable=False` reste juste** — expliqué en commentaire, pas changé : le seul défaut
  connu qui corrompt silencieusement un envoi est désormais rejoué *avant* d'atteindre ce point.
- **Preuve apportée** : 7 tests d'adaptateur contre un faux serveur HTTP (corruption confirmée,
  épuisement des tentatives, absence du champ `size`, panne pendant la vérification, échec du
  nettoyage best-effort). **Aucune preuve contre un RAGFlow réel** — la machine ne permettait pas
  de démarrer la stack de dev sans risque pour la production (swap déjà plein, cf. §3).
- **Les deux pistes d'infrastructure d'ADR-044 §5.1 restent non appliquées**, avec un verdict
  chiffré cette fois (§4) : la borne de concurrence Celery n'a plus le même poids qu'avant ce
  correctif, et le serveur WSGI de production n'est pas la substitution propre qu'on pouvait
  espérer — vérifié sur l'image réellement déployée, pas supposé.

## Contexte

ADR-044 §5.1 (2026-08-29) avait établi la cause du flake d'ingestion des tableurs (~1 tentative sur
3) : corruption du corps d'un envoi multipart par le serveur de développement Werkzeug de RAGFlow
sous requêtes concurrentes. Sa correction du 2026-09-13 a établi que la mitigation citée
(`ENGINE_TRANSIENT_RETRY_CODES`, `run.py` (suite de mesure interne)) n'existe que dans le harnais
de mesure : le chemin de production (`_poll`, `backend/app/workers/tasks/ingestion.py`) code en dur
`retryable=False` pour `engine_processing_failed`, donc un job réel touché échoue définitivement,
récupérable seulement par un `POST /jobs/{id}/retry?force=true` manuel.

## Décision

### 1. Le rejeu, dans l'adaptateur

`RagflowEngineAdapter.upload_document` (`backend/app/providers/engine/ragflow/client.py`) vérifie
désormais chaque upload avant de le rendre à l'appelant :

1. `POST /datasets/{id}/documents` — comme avant, un identifiant de document valide revient même
   sur un envoi corrompu (`code=0` : `_call` ne voit rien, la corruption ne se révèle que dans le
   contenu stocké).
2. `_uploaded_size` relit la taille enregistrée côté moteur (`GET /datasets/{id}/documents?id=…`,
   même endpoint que `get_parse_status` — aucun endpoint de métadonnées par identifiant n'existe).
3. Une taille qui ne correspond pas à celle envoyée, ou une panne transitoire pendant cette
   relecture elle-même, déclenchent le rejeu : le document est supprimé (`delete_document`,
   best-effort — son propre échec ne bloque pas la suite), puis retéléversé.
4. Borné à 4 tentatives (`_UPLOAD_INTEGRITY_MAX_ATTEMPTS`), attente croissante 1/2/4 s
   (`_UPLOAD_RETRY_BACKOFF_SECONDS`) entre deux essais, chaque rejeu journalisé (`extra=` : nom de
   fichier, identifiant de document, motif, numéro de tentative). Au-delà, `EngineTransientError` —
   la même famille que toute panne moteur momentanée, jamais une boucle sans fin.

**Distinction rejouable / permanent, reprise telle quelle** : seule l'incohérence de taille (ou une
panne pendant sa vérification) déclenche ce rejeu. Un `EngineValidationError` (argument rejeté,
`code=101`) ou une `EngineError` générique (`code=102`, document/dataset introuvable) ne sont jamais
concernés — les rejouer à l'identique échouerait pareil, exactement le principe déjà en place pour
`_TRANSIENT_CODES`/`_VALIDATION_CODES`.

**Idempotence.** RAGFlow ne déduplique jamais un document par lui-même — fait déjà établi pour le nom
de dataset (`dataset_name_for`, ADR-012 décision 3 : `audit-kb` → `audit-kb-renamed(1)` plutôt qu'un
refus) et confirmé structurellement identique pour un document : chaque `POST` réussi crée une entrée
distincte, corrompu ou non. Un rejeu qui se contenterait de retéléverser sans supprimer l'ancien
laisserait donc un document fantôme par tentative — jamais indexé (on ne l'a jamais soumis au
découpage), donc invisible en recherche, mais un coût de stockage et de clarté qui grandit à chaque
flake. La suppression avant nouvel essai ferme ce risque.

**`get_parse_status`/`_poll` n'ont pas changé de comportement.** Un commentaire a été ajouté au point
`status.run == "fail"` de `_poll` (`backend/app/workers/tasks/ingestion.py`) : `retryable=False`
reste juste, parce que le seul défaut connu qui corrompt silencieusement un envoi est désormais
intercepté *avant* `start_parsing` — un `engine_processing_failed` qui atteint quand même ce point
est un fichier réellement invalide pour le moteur, ou une panne que cette vérification ne couvre pas
(rejeu épuisé, ou corruption d'un appel que l'adaptateur ne vérifie pas). Dans les deux cas, rejouer
automatiquement en boucle ici serait exactement le défaut que le rejeu de l'adaptateur existe pour
éviter ; `POST /jobs/{id}/retry` reste le geste explicite.

### 2. Pourquoi la vérification porte sur `upload_document` seul

Les deux cas de corruption observés (`.xlsx`, `.jpg`, ADR-044 §5.1) sont tous deux des corps
multipart binaires. Les autres appels de l'adaptateur qui écrivent (`PATCH`/`POST` de
`start_parsing`, `POST`/`DELETE` du reste de l'API) transportent du JSON : un corps JSON de
quelques dizaines d'octets corrompu par le même mécanisme casserait très probablement le parsing
JSON lui-même côté RAGFlow (réponse `400`/erreur `code` explicite), plutôt que d'être stocké tel
quel et découvert seulement au parsing — l'asymétrie observée (le bug touche des fichiers, jamais
des instructions) est cohérente avec cette explication, mais **n'a pas été testée** sur ces autres
appels : rien n'exclut formellement une corruption silencieuse d'un petit corps JSON qui resterait
syntaxiquement valide. Si un `engine_processing_failed` inexpliqué réapparaît malgré ce correctif,
regarder d'abord si son origine est `start_parsing` plutôt que `upload_document`.

### 3. Ce qui a été prouvé, et ce qui reste à vérifier

**Prouvé** (`backend/tests/providers/engine/test_ragflow_client.py`, 7 tests contre un `MockTransport`
qui rejoue des réponses scriptées, sans réseau) :

- un envoi dont la taille stockée diffère de la taille envoyée est supprimé puis retéléversé, et le
  second identifiant est bien celui rendu à l'appelant ;
- une corruption persistante épuise les 4 tentatives et remonte `EngineTransientError`, jamais une
  boucle infinie ;
- l'absence du champ `size` dans la réponse (moteur qui ne le rendrait pas) désactive la
  vérification sans faire échouer l'upload ;
- une panne transitoire pendant la relecture de la taille déclenche le même rejeu qu'une
  incohérence confirmée — jamais traitée comme un envoi intact faute de mieux ;
- l'échec du nettoyage (`DELETE`) ne bloque pas la nouvelle tentative.

**Non vérifié contre un RAGFlow réel** — la machine de développement tournait avec le swap plein
(`free -g` : 0 Go libre, 7/7 Go d'échange utilisés) et la stack de production RAGFlow déjà active
(`ragflow-prod-*`, Elasticsearch + MySQL + MinIO + Valkey + le serveur applicatif) ; démarrer une
seconde stack complète pour la dev aurait mis la production en danger de contention mémoire, exclu
par le brief de cette session. Restent donc à confirmer sur une instance réelle :

1. **Le nom exact et la présence du champ `size`** dans `GET /datasets/{id}/documents?id=…` — sa
   provenance ici est la lecture d'ADR-044 §5.1 (« RAGFlow enregistre `size=5050` »), pas une
   requête que cet adaptateur a rejouée lui-même dans cette session. `_uploaded_size` dégrade
   silencieusement (vérification désactivée) si le champ est absent, donc une erreur de nom ne
   casse rien — mais ne protège pas non plus tant qu'elle n'est pas corrigée.
2. **La disparition effective du flake** en conditions réelles : reproduire la corruption sous
   charge concurrente (comme l'audit d'ADR-044 §5.1 l'avait fait) et vérifier que le rejeu la
   rattrape de bout en bout, sur un vrai `.xlsx` binaire, via le pipeline complet (pas seulement
   l'adaptateur isolé).
3. les résultats de mesure internes : une exécution de `test_office.py` (suite de mesure interne) contre une instance de
   dev, pour re-caractériser le taux d'échec résiduel après ce correctif (attendu ≈2 %, cf. §4).

### 4. Verdict chiffré sur les deux pistes d'infrastructure d'ADR-044 §5.1

**Piste A — borner `CELERY_DEFAULT_CONCURRENCY` (défaut 4, `docker/docker-compose.yml`).**

Écartée comme réglage par défaut, recommandée comme option documentée pour qui veut aller plus loin
que le rejeu seul :

- Le rejeu de cette ADR ramène la probabilité d'échec résiduel après upload de ~37,5 % (une
  tentative) à **≈2 % (0,375⁴, 4 tentatives)** — le gain marginal d'une concurrence réduite est donc
  nettement plus petit qu'avant ce correctif, puisque l'essentiel du problème est déjà absorbé sans
  toucher au débit.
- Le coût n'est **pas** un facteur 4 uniforme, contrairement à une lecture rapide de la valeur du
  réglage : la queue `default` mélange des étapes à travail borné (`run_precheck`, `run_chunking` —
  un seul passage, proportionnellement affecté par la concurrence) et des étapes de sondage
  (`run_embedding`, `run_indexing`) qui **ne retiennent jamais un worker entre deux sondages**
  (`_reschedule`, `apply_async(countdown=…)` — le worker est rendu immédiatement, la tâche se
  replanifie). Le goulot réel d'une concurrence réduite est donc l'**admission** de nouveaux
  documents en `chunking` (upload + profil + lancement), pas le nombre de documents pouvant être
  *mid-ingestion* simultanément.
- **Chiffre non mesuré, à établir avant d'agir** : cette session n'a pas pu mesurer le temps réel
  d'une exécution de `_chunk_document` (lecture stockage + upload + vérification d'intégrité +
  `PATCH`/`POST` de lancement) sous charge, faute de pouvoir démarrer RAGFlow de dev sans risque
  (§3). Sans cette mesure, passer de 4 à 1 est un pari, pas une décision instruite — exactement ce
  que le brief de cette ADR interdit d'affirmer sans preuve.
- **Ce qu'il faudrait pour trancher** : ingérer un lot de documents identiques en parallèle contre
  une instance de dev à `CELERY_DEFAULT_CONCURRENCY=4` puis `=1`, mesurer le temps écoulé entre le
  dépôt du dernier document et son entrée en `chunking` dans les deux cas. `RAGENGINE_PROJECT_NAME`
  distinct de la prod (ADR-057) rend cette mesure possible sans toucher à la production.

**Piste B — servir RAGFlow par un serveur WSGI de production.** Écartée, avec une preuve structurelle
plutôt qu'une simple prudence :

- `api/ragflow_server.py` (inspecté directement dans l'image déployée,
  `ragengine/ragflow-cpu:v0.26.4-ragengine.2`, sans démarrer aucun conteneur — `docker run --rm
  --entrypoint cat … /ragflow/api/ragflow_server.py`) place `app.run(...)` dans son bloc
  `if __name__ == "__main__":`, mais ce bloc fait bien plus que démarrer le serveur : initialisation
  des tables (`init_web_db`, `init_web_data`), du `RuntimeConfig`, des gestionnaires de signaux, du
  chargement des plugins (`GlobalPluginManager.load_plugins()`), et surtout le démarrage d'un
  **thread de fond** (`delayed_start_update_progress` → `DocumentService.update_progress()`, boucle
  toutes les 6 s) — c'est probablement ce thread qui synchronise `run`/`progress`/`chunk_count`, les
  champs que `get_parse_status` de RAGEngine sonde. Remplacer l'entrypoint par un simple
  `gunicorn api.ragflow_server:app` **n'exécuterait jamais ce bloc** (le module serait importé, pas
  lancé comme script) : le serveur HTTP répondrait, mais rien ne prouve que la progression des
  documents continuerait à être synchronisée. Reproduire ce bloc dans un script wrapper reviendrait
  à réimplémenter une brique d'orchestration de RAGFlow — exactement ce que CONTRIBUTING.md §3 et le
  périmètre de cette tâche interdisent.
- **La piste voisine envisagée — le serveur applicatif Go intégré à RAGFlow** (`API_PROXY_SCHEME
  =golang`, déjà câblé en variable d'environnement dans `docker/docker-compose.ragflow.yml`, non
  activé) **a été vérifiée et écartée sur preuve directe** : `entrypoint.sh` lance ce serveur depuis
  `bin/ragflow_server`, et ce répertoire est **vide** dans l'image réellement construite
  (`ragflow-cpu.Dockerfile`) — seul un `.gitkeep`. Basculer `API_PROXY_SCHEME` sur `golang`
  casserait tout, nginx proxyant vers un binaire absent. Ni une option de configuration propre, ni
  réversible sans reconstruire l'image avec une chaîne de build Go ajoutée — hors périmètre.
- **Aucune dépendance WSGI de production (gunicorn/waitress/uwsgi/gevent) n'est installée dans
  l'image** (vérifié : `pip list` dans un conteneur jetable, aucune correspondance) : le poser
  demanderait de toute façon de modifier `ragflow-cpu.Dockerfile`, pas seulement une commande ou un
  entrypoint de service — hors de la condition posée pour cette piste (« propre et réversible par
  configuration »).
- **Ce qu'il faudrait pour la rouvrir** : soit obtenir/valider une image RAGFlow où `bin/
  ragflow_server` est réellement construit (variante amont non distribuée dans ce dépôt à ce jour),
  soit écrire un point d'entrée WSGI qui réplique fidèlement le bloc `__main__` de
  `ragflow_server.py` (risque de divergence à chaque montée de version RAGFlow, à documenter comme
  dette si jamais entrepris) et le vérifier de bout en bout (démarrage propre, synchronisation de
  progression, absence de corruption sous charge) sur une instance de dev isolée.

## Conséquences

- Un job réel touché par la corruption d'upload RAGFlow n'échoue plus systématiquement : le rejeu
  de l'adaptateur absorbe l'écrasante majorité des cas avant qu'aucune exception n'atteigne le
  worker. Le résidu (≈2 % par upload, non mesuré en conditions réelles) reste un échec définitif,
  récupérable par `POST /jobs/{id}/retry`, comme documenté en `_poll`.
- Aucun changement d'infrastructure appliqué : les deux pistes d'ADR-044 §5.1 restent des options,
  pas des correctifs déployés. La piste concurrence reste ouverte mais non chiffrée faute de mesure
  possible cette session ; la piste WSGI/Go est fermée sur preuve structurelle, pas seulement par
  prudence.
- l'index interne des rapports de mesure : aucun dossier de résultats de mesure internes produit par cette session (pas
  de campagne de mesure exécutée, cf. §3) — rien à y consigner pour l'instant.
