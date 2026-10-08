# ADR-040 — Rate limiting : plafond IP dédié pour la passerelle Docker locale

**Date :** 2026-08-19 · **Statut :** accepté · **Portée :** `app/core/rate_limit.py`, `app/core/config.py`

## Contexte

Utilisateur bloqué à la connexion (« Trop de requêtes : réessayez plus tard »). Journaux réels :
14 réponses `429` dans la même seconde, toutes avec la même IP cliente, `172.18.0.1` — confirmé
comme la passerelle du réseau Docker bridge `ragengine` (`docker network inspect ragengine` :
`Gateway: 172.18.0.1`, sous-réseau `172.18.0.0/16`). La dernière ligne était `POST
/api/v1/sessions HTTP/1.1" 429` : la tentative de connexion elle-même, coupée par un burst
d'endpoints de suivi de tableau de bord sans rapport (`/documents/{id}`, `/knowledge-bases/{id}`,
`/me`), parti d'un autre onglet.

**Absence de proxy inverse confirmée.** `docker/docker-compose.yml` publie `api` directement sur
`127.0.0.1:${RAGENGINE_API_PORT}` (pas de service `nginx`/`traefik` devant elle) ; aucune lecture
de `X-Forwarded-For`/`X-Real-IP` nulle part dans `backend/app/` (`grep` sur le dépôt). Le navigateur
et tout outil local parlent donc directement au port publié — c'est ce chemin, pas un en-tête de
proxy à faire confiance, qui explique l'IP observée.

**Mécanisme, déjà dans le code.** `RateLimitMiddleware.dispatch` vérifie le compteur IP
(`ratelimit:ip:{client_ip}`, `rate_limit_anonymous_requests`, défaut 60/60 s) **en premier,
systématiquement, pour toute requête** — avant même de regarder un cookie de session ou une clé
API. Le compteur généreux ajouté par ADR-036 (session/clé, 120/60 s) est **additionnel**, jamais
substitutif : il ne dispense pas du compteur IP, il s'ajoute par-dessus. En déploiement local (le
cas par défaut de ce dépôt), `api` n'est jamais contactée directement par le navigateur : la
publication de port Docker fait qu'*tout* trafic issu de la machine hôte — tous onglets, toutes
sessions, tout script local — arrive sous cette même IP de passerelle. Un plafond calibré pour un
visiteur anonyme isolé (60/min) revient alors à plafonner *tout le déploiement local* comme un
inconnu, et une requête peut être bloquée sans aucun rapport avec celle qui a rempli le
compartiment — exactement le `POST /api/v1/sessions` en `429` du journal, qui n'a par construction
aucun cookie de session (on ne s'est pas encore connecté) et n'a donc jamais accès au compteur
généreux.

**Reproduit contre la stack réelle** (Redis/API en cours d'exécution, pas de fixture) : un burst
de 65 `GET /api/v1/providers` anonymes depuis l'hôte donne 60×`200`/5×`429` (plafond 60/60 s par
défaut, atteint), et un `POST /api/v1/sessions` immédiatement après reçoit **`429`** — la panne
observée par l'utilisateur, reconstituée à l'identique en local.

## Décision

**Piste A retenue : plafond IP dédié, plus généreux mais toujours borné, pour l'IP détectée comme
passerelle Docker locale.** `_detect_docker_gateway_ip()` (`app/core/rate_limit.py`) lit la route
par défaut du conteneur lui-même dans `/proc/net/route`, gardée par la présence de `/.dockerenv`
— **jamais une adresse codée en dur** : le sous-réseau et donc la passerelle sont attribués par
Docker et diffèrent d'un déploiement à l'autre (réseau bridge personnalisé, plusieurs projets
Compose sur la même machine…). C'est fiable par construction du driver `bridge` : tout conteneur
qui y est attaché reçoit une route par défaut vers l'adresse `.1` du sous-réseau, et c'est cette
même adresse que le hairpin NAT de la publication de port substitue à la source réelle pour tout
appelant qui contacte ce port depuis l'hôte — ce n'est pas une coïncidence à revalider à chaque
déploiement, c'est le même mécanisme réseau des deux côtés (vérifié dans le conteneur
`ragengine-api` : `/proc/net/route` y donne `172.18.0.1`, identique à `docker network inspect`).

Cette IP reçoit `rate_limit_gateway_requests` (nouveau réglage, défaut **600/60 s**, dix fois le
plafond anonyme) à la place de `rate_limit_anonymous_requests` — généreux pour absorber plusieurs
onglets/sessions d'un même déploiement, mais **toujours borné** : une boucle d'abus réellement
locale reste freinée, seulement à un débit dix fois plus élevé. Le compteur généreux (session/clé,
ADR-036) et le compteur IP restent deux mécanismes distincts et cumulatifs, inchangés l'un envers
l'autre — seul le plafond du compteur IP varie selon l'adresse.

**Garde-fou de portabilité.** La détection exige `/.dockerenv` (marqueur standard posé par Docker
dans tout conteneur) avant de faire confiance à la route par défaut : hors d'un conteneur
(déploiement bare-metal), cette route est celle de l'infrastructure réseau réelle du serveur — box
internet, routeur d'entreprise — potentiellement une adresse par laquelle passeraient de vrais
visiteurs anonymes partagés (NAT). Lui accorder le plafond généreux affaiblirait la protection pour
un cas que ce correctif ne vise pas. Toute condition d'échec de la détection (fichier absent,
format inattendu, plateforme non Linux) renvoie `None` et retombe silencieusement sur le plafond
anonyme strict — jamais de plafond élevé accordé par défaut sur une détection qui a échoué.
`RateLimitMiddleware` accepte aussi un `gateway_ip` explicite (paramètre constructeur), pour les
tests et pour un opérateur qui voudrait un jour forcer une valeur plutôt que la détection
automatique.

## Piste B (polling frontend) évaluée, non retenue seule

`JobTracker` (`frontend/src/features/ingestion/JobTracker.tsx`) fait interroger
`GET /v1/jobs/{jobId}` par `jobQueryOptions` (`frontend/src/api/jobs.ts`) à intervalle fixe sans
gigue (`FAST_INTERVAL_MS = 1200`, `SLOW_INTERVAL_MS = 2500`) : plusieurs documents suivis en
parallèle (upload par lot) synchronisent effectivement leurs relevés. C'est un facteur de charge
réel, mais **ça n'explique pas le défaut observé** : le dernier appel bloqué était
`POST /api/v1/sessions`, une requête de connexion qui, par construction, ne porte encore aucun
cookie de session — aucune quantité de gigue côté polling ne change qu'elle retombe, comme toute
requête anonyme, sur le seul compteur IP partagé par la machine entière. Ajouter de la gigue
réduirait la probabilité de bursts synchronisés sans changer le fait structurant : sur un
déploiement local, un plafond anonyme pensé pour un visiteur isolé reste, par définition, partagé
par tout ce qui tourne sur la machine hôte — un second onglet, un autre utilisateur du même poste,
ou simplement une session qui suit plusieurs documents à la fois peut à lui seul l'épuiser, gigue
ou non. Retenir Piste A seule évite un second changement (coût de maintenance, ADR-032 sur les
tests frontend recommencerait) pour un gain marginal sur un défaut déjà résolu à la racine.

## Vérification

Reproduit contre la stack réelle (voir « Contexte » — Redis/API réellement démarrés, pas de mock) :

- **Avant correctif** : burst de 65 `GET` anonymes → 60×`200`, 5×`429` ; `POST /api/v1/sessions`
  immédiatement après → **`429`** (panne reproduite à l'identique).
- **Après correctif**, redéployé (`docker compose up -d --build api`) : même burst de 65 `GET`
  anonymes depuis l'hôte (IP passerelle réelle `172.18.0.1`, plafond 600/60 s) → 65×`200` ;
  `POST /api/v1/sessions` immédiatement après → **`200`**-classe (`401`, identifiants bidons —
  jamais `429`).
- **Cas négatif (abus réel toujours freiné)** : `backend/tests/test_rate_limit.py::
  test_a_real_external_ip_is_still_capped_by_the_strict_anonymous_limit` — une IP qui n'est *pas*
  celle détectée comme passerelle reste bloquée au plafond anonyme strict (2/2 requêtes admises,
  3e `429`) même quand un plafond passerelle généreux (100) est configuré par ailleurs.

## Conséquences

- Nouveau réglage `RAGENGINE_RATE_LIMIT_GATEWAY_REQUESTS` (défaut `600`), documenté dans
  `.env.example`.
- `RateLimitMiddleware.__init__` gagne un paramètre `gateway_ip` (sentinel `_UNSET_GATEWAY_IP` par
  défaut = détection automatique ; `None` explicite = jamais de traitement spécial, comportement
  strictement identique à avant cet ADR).
- `_detect_docker_gateway_ip()` est mémoïsée (`@lru_cache`) : une lecture de `/proc/net/route` par
  process, pas par requête.
- `backend/tests/test_rate_limit.py` : 7 tests ajoutés — détection pure (route lue, absence de
  `/.dockerenv`, échec sans lever), plafond passerelle appliqué, IP externe toujours bridée par le
  plafond anonyme, `gateway_ip=None` neutralise le traitement spécial, et un scénario bout en bout
  (burst de polling + connexion qui n'est plus bloquée).
- Aucun changement côté frontend (Piste B non retenue) : `JobTracker`/`jobQueryOptions`
  inchangés.

## Complément (2026-09-13) — clé IP normalisée par `/64` (ADR-052)

Le compteur `ratelimit:ip:{client_ip}` comptait chaque IPv6 individuellement : un attaquant en
IPv6 change d'adresse à volonté dans son propre `/64` (généralement alloué en entier par son FAI)
sans aucun coût, videant ce plafond de tout effet, à la différence d'une IPv4 (une seule adresse
par attaquant, en pratique). `normalize_client_ip` (`app/core/rate_limit.py`) ramène désormais
toute IPv6 à son préfixe `/64` avant construction de la clé — IPv4 inchangée, IPv4 mappée en IPv6
(`::ffff:a.b.c.d`) ramenée à l'IPv4 qu'elle encode. La comparaison à `_gateway_ip` (passerelle
Docker, ci-dessus) reste valide sans changement : la passerelle détectée est toujours une IPv4.
Jamais appliqué à `sessions.ip_address` (valeur forensique exacte, hors périmètre de ce compteur).
Même normalisation appliquée au plafond dédié d'un chatbot publié (`ratelimit:chatbot:{slug}:{ip}`)
et au quota anonyme de dépense LLM (ADR-054). Tests : `backend/tests/test_rate_limit.py`, section
« Normalisation de la clé d'identité IP ».
