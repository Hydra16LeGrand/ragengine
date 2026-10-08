# ADR-064 — Cookie de session : préfixe `__Host-` et durée de vie réduite

**Date :** 2026-09-18 · **Statut :** accepté · **Origine :** audit de sécurité 2026-09, constat 4
(reliquat après `SESSION_COOKIE_SECURE`, déjà traité)

## Contexte

Le constat 4 avait deux volets. `SESSION_COOKIE_SECURE` forcé en staging/production est déjà en
place (validator `_require_secure_session_cookie_when_exposed`). Restaient hors périmètre : le
préfixe `__Host-` et la durée de vie de 720 h (30 jours).

## Décision

**Préfixe `__Host-`, dérivé du même calcul que `session_cookie_secure`.** `__Host-` impose au
navigateur trois garanties (`Secure`, `Path=/`, aucun `Domain`) que ce cookie tient déjà — l'ajouter
ne change aucun attribut, il fait juste vérifier ces trois-là par le navigateur lui-même. Il ne peut
pas s'appliquer en développement : `__Host-` exige `Secure`, donc HTTPS, absent sur
`http://localhost`. `Settings.session_cookie_effective_name` (`backend/app/core/config.py`)
préfixe `session_cookie_name` quand `_is_exposed_environment` (la même fonction que pour
`session_cookie_secure`) vaut vrai, sans double-préfixage si l'opérateur a déjà nommé son cookie
`__Host-...`.

**Toute lecture ou écriture du cookie passe par cette propriété, jamais par `session_cookie_name`
brut** : `set_session_cookie`/`clear_session_cookie`/`read_session_token`
(`app/api/v1/dependencies.py`) et `_session_token` (`app/core/rate_limit.py`, compteur de rate
limiting par session) — quatre points, tous mis à jour. Un seul oublié aurait rendu le cookie
illisible par le serveur qui l'a lui-même émis (le rate limiting serait retombé silencieusement sur
le plafond IP pour tout le trafic authentifié par cookie en staging/production). Prouvé par un test
d'intégration HTTP réel (`backend/tests/api/v1/test_session_cookie_hardening.py`) : login en
environnement `staging` → `Set-Cookie: __Host-ragengine_session=...; Secure` → rejeu du cookie par
le client → `GET /me` répond `200`, pas seulement une assertion sur la propriété en isolation.

**Durée de vie réduite de 720 h (30 jours) à 168 h (7 jours).** Pas de renouvellement glissant :
l'implémenter aurait exigé de garantir qu'il ne rende jamais la session éternelle (glissement à
l'infini si l'utilisateur reste actif), une garantie supplémentaire à construire et tester pour un
gain marginal ici. Sans lui, la valeur choisie doit rester praticable pour un usage réel — une
ingestion ou une revue de precheck peut s'étaler sur plusieurs jours — donc pas aussi bas que le TTL
d'un espace invité (24 h, `GUEST_SESSION_TTL_HOURS`, données jetables par construction). 7 jours
borne l'exposition d'un jeton intercepté à un septième de l'ancienne valeur, un ordre de grandeur
usuel pour une session « me souvenir de moi » sans case à cocher dédiée, tout en restant praticable
pour un usage hebdomadaire actif.

## Conséquences

- **Rupture assumée au déploiement** : le nom de cookie change en staging/production
  (`ragengine_session` → `__Host-ragengine_session`). Tout visiteur avec une session active au
  moment du déploiement est déconnecté — le navigateur ne renvoie plus l'ancien cookie sous le
  nouveau nom, et le serveur ne cherche plus l'ancien nom. Pas de migration possible : c'est
  precisément la garantie `__Host-` (aucune confusion entre cookies de portées différentes) qui
  l'impose.
- Toute session existante, même avec l'ancien TTL de 30 jours, est de toute façon invalidée par
  corollaire du point précédent (nouveau nom = nouveau cookie).
- `.env.example` documente `SESSION_TTL_HOURS=720  # 30 jours` — à mettre à jour vers
  `168  # 7 jours` (hors périmètre de cette session, cf. rapport).
- Tests : `backend/tests/test_config.py` (nom effectif non préfixé en développement, préfixé en
  staging/production, pas de double préfixe, nouveau défaut de TTL) ;
  `backend/tests/api/v1/test_session_cookie_hardening.py` (aller-retour HTTP réel en
  environnement `staging`).
