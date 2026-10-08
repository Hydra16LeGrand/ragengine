# ADR-015 — `DbSession` referme sa transaction avant l'envoi de la réponse

**Date :** 2026-07-27 · **Statut :** accepté

## Résumé

- FastAPI (0.140.7) ferme une dépendance `yield` **après** l'envoi de la réponse
  par défaut (`scope="request"`) — vérifié à la source et empiriquement, pas une
  supposition.
- Conséquence : le `COMMIT` de fin de requête de `get_db_session` n'avait pas
  encore eu lieu quand le client recevait sa réponse. Un second appel enchaîné
  aussitôt (register → login, tout `POST` suivi d'un `GET`) pouvait lire l'état
  précédent sous `READ COMMITTED`, sans erreur.
- Constaté en conditions réelles (test de bout en bout Phase 4, pas en test
  unitaire) : ~30 % des `POST /sessions` juste après `POST /users` renvoyaient
  `401`.
- Correctif : `DbSession = Annotated[AsyncSession, Depends(get_db_session,
  scope="function")]` (`app/db/session.py`). `scope="function"` referme la
  dépendance avant que la réponse ne soit sérialisée — le commit est garanti
  visible dès le premier octet reçu par le client.

## Contexte

Le pipeline d'ingestion (Phase 4) a révélé un premier symptôme de cette classe de
bug : `decide_precheck` dispatchait `run_chunking` via `BackgroundTasks` en
supposant que les tâches de fond s'exécutent après le commit de la session — faux
dans cette version de FastAPI (ADR précédente implicite, corrigée par un commit
explicite avant publication, `DocumentService._publish_after_commit`). Le test de
bout en bout qui a suivi (trois documents de référence, requêtes HTTP réelles
contre la stack déployée plutôt que la suite pytest en mémoire) a révélé que le
même défaut d'ordonnancement touchait **toute requête**, pas seulement celles qui
publient une tâche Celery : n'importe quel enchaînement de deux requêtes
dépendantes pouvait courir contre son propre commit.

La suite pytest existante n'a jamais vu ce défaut : `db_session` (transaction
externe + points de sauvegarde) et les clients `ASGITransport` in-process
n'exposent pas la latence réseau qui rend la course observable. Seul un aller-retour
réseau réel (conteneur Docker réellement exposé, deux requêtes HTTP distinctes)
la rend visible — d'où son absence de toute la suite de tests jusqu'ici.

## Décision

`DbSession` (seule façon d'obtenir une session dans un routeur, `app/db/session.py`)
passe en `scope="function"`. La dépendance s'exécute intégralement — y compris son
`COMMIT`/`ROLLBACK` de sortie — **autour de la fonction de route**, avant toute
sérialisation ou envoi de réponse.

Aucun autre changement n'était nécessaire : `expire_on_commit=False` sur la
fabrique de sessions (`get_session_factory`) garantit que les objets restent
lisibles pour la sérialisation de la réponse après ce commit anticipé, sans
requête supplémentaire ; aucun routeur n'utilise plus `BackgroundTasks` avec la
session de requête (le seul cas, `decide_precheck`, a déjà son commit explicite,
désormais redondant mais inoffensif — la garde `_publish_after_commit` reste en
place par défense en profondeur, pas retirée).

## Conséquences

- Correction de fond, pas seulement pour l'ingestion : **toute** paire de
  requêtes dépendantes (créer puis lire, créer puis agir) est désormais garantie
  cohérente, y compris pour le futur frontend SPA qui enchaînera des appels
  `fetch()` sans délai artificiel.
- `session_scope()` (hors requête HTTP — tâches Celery, scripts) n'est pas
  concerné : il n'a jamais dépendu du cycle de vie d'une réponse FastAPI.
- Vigilance pour toute future dépendance `yield` scoped à la requête (aucune
  autre aujourd'hui) : le même défaut d'ordonnancement s'applique par défaut,
  `scope="function"` doit être un choix explicite dès sa création, pas un
  correctif après coup.
