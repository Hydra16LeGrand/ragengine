# ADR-058 — Rotation de session à l'élévation d'un invité en compte

**Date :** 2026-09-16 · **Statut :** accepté · **Origine :** audit de sécurité 2026-09, constat 7

## Contexte

`PUT /api/v1/me/credentials` (`UserService.elevate_guest`) mute la ligne `User` en place
(ADR-009 : même `id`, même travail attaché) mais ne touchait jamais à `sessions`. Un attaquant
qui aurait fait poser son propre cookie invité chez la victime (XSS sur un sous-domaine, ou MITM
sur `http://`) voyait ce cookie continuer d'authentifier le compte réel une fois la victime
inscrite : l'identifiant de session ne changeait jamais à travers la frontière de privilège.

## Décision

`SessionService.rotate_after_elevation(user, ...)` révoque **toutes** les sessions déjà actives
de la ligne (pas seulement celle qui a authentifié la requête — deux onglets ou un cookie copié
laisseraient sinon un accès residuel) puis en émet une neuve, avec les mêmes attributs de cookie
que partout ailleurs (`set_session_cookie` : `HttpOnly`, `SameSite=Lax`, `Secure` déduit de
l'environnement). La route `PUT /me/credentials` appelle `UserService.elevate_guest` puis cette
rotation, dans cet ordre, avant de réémettre le cookie.

La révocation passe par l'ORM (comme `SessionService.revoke`), pas un `UPDATE` en masse : la
session qui vient d'authentifier la requête d'élévation peut déjà être chargée dans le contexte
courant, et doit refléter sa révocation sans rechargement explicite.

**Autres transitions de privilège du même fichier, vérifiées** : `login()` émet toujours une
session neuve via `_issue()` quel que soit le cookie déjà présent dans la requête — aucune
fixation possible là, une connexion ne réutilise jamais un identifiant existant. Aucun
changement de mot de passe n'existe pour un compte réel déjà pourvu d'identifiants (seule
`elevate_guest` en pose un, une fois, à la conversion) : rien d'autre à corriger dans ce
périmètre.

## Conséquences

- Un cookie invité intercepté avant l'élévation cesse de fonctionner dès que la victime s'inscrit ;
  le nouveau cookie, transmis dans la même réponse, poursuit son parcours sans interruption
  perceptible.
- Les données de l'espace invité (bases, documents, jobs) restent attachées au même `user_id` :
  seule la table `sessions` est affectée, `UserService.elevate_guest` est inchangé.
- Tests : `backend/tests/services/identity/test_sessions.py` (rotation multi-sessions, jeton
  distinct), `backend/tests/api/v1/test_identity_routes.py::test_the_stale_cookie_is_revoked_at_elevation`
  (ancien cookie rejoué de force → `401`, nouveau cookie → `200`).
