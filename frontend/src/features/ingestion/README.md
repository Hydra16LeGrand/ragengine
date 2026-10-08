# `ingestion/`

Parcours d'ingestion de documents (Schéma A) : dépôt de fichiers, écran de **precheck
interactif** où l'utilisateur valide la complexité détectée (tableaux, scans/OCR, langues
identifiées), puis progression pas à pas du job jusqu'à l'indexation.

Consomme `POST /api/v1/knowledge-bases/{kb_id}/documents/ingest`,
`GET /api/v1/documents/{document_id}`, `PUT /api/v1/documents/{document_id}/precheck/decision`
et le polling de `GET /api/v1/jobs/{job_id}`.

## Fichiers

| Fichier              | Rôle                                                                                                             |
| -------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `UploadPanel.tsx`    | Zone de dépôt + file d'attente ; orchestre les envois                                                            |
| `JobTracker.tsx`     | Suivi d'un document : polling, aiguillage, progression, échec, annulation                                        |
| `PrecheckReview.tsx` | Restitution du rapport de precheck et des deux options de découpage                                              |
| `JobOutcome.tsx`     | Rendu de l'issue d'un job — cause d'échec, avertissement qualité ; partagé avec `knowledge-bases/DocumentsPanel` |
| `failureCause.ts`    | Règles pures : message brut du moteur → constat + conseil (aucun rendu)                                          |
| `jobSteps.ts`        | Traduction `job.state` → étapes affichables (aucune autre entrée)                                                |

Point d'entrée : `UploadPanel`, monté dans le détail d'une base, **au-dessus** du panneau des
documents en lecture seule — le versement est l'action qu'on vient faire, la liste est ce qu'on
vérifie ensuite.

## Décisions structurantes

**La machine à états n'est jamais dérivée côté client.** `GET /jobs/{id}.state` est la seule
vérité : `jobSteps.ts` prend un état et rend des étapes, sans horodatage, sans mémoire de l'état
précédent, sans `progress_percent`. Une étape n'est « franchie » que parce que la façade a
annoncé un état postérieur.

**Pas de barre continue.** `progress_percent` vaut `0` pendant tout le traitement (ADR-012 : le
moteur ne publie pas d'avancement fiable). L'afficher montrerait une barre figée pendant des
minutes ; l'interpoler inventerait une durée. On montre donc l'étape franchie, un fait
vérifiable — c'est le contrat de `StepProgress`, qui refuse tout ratio par construction.

**L'échec ne désigne aucune étape.** `failed` remplace l'état de travail : le job ne dit plus
_où_ il en était. Retenir le dernier état observé serait faux dès qu'une étape passe entre deux
relevés (1 à 3 s). L'écran remplace donc la progression par un bloc d'échec — message, code,
réparabilité — plutôt que de pointer une étape au hasard.

> ⚠️ Contrat backend observé en Phase 7 : un job peut finir `failed` avec `error: null`, alors
> que le worker a journalisé une cause précise. Le bloc d'échec se dégrade proprement (titre et
> nombre de tentatives, sans message), mais l'utilisateur reste sans explication tant que la
> façade ne persiste pas de `JobError`.

**La cause survit à la session de versement** (ADR-041). `JobOutcome` est monté à la fois ici et
par la liste des documents, qui relit `GET /jobs/{current_job_id}` **au dépli** : jusqu'au
2026-08, la cause n'existait que dans `JobTracker` et disparaissait au premier rafraîchissement,
ce qui a produit à lui seul plusieurs « échec sans cause visible » pendant la campagne de tests
manuels.

**Une cause reconnue passe devant, le dump du moteur reste dessous.** `failureCause.ts` éprouve
des motifs sur le **message** (`RESOURCE_EXHAUSTED`, `not supported yet`,
`cannot identify image file`) et non sur le code : le moteur range des causes sans rapport sous
le même `engine_processing_failed`. Aucun motif ne correspond ⇒ rien n'est inventé, le message
brut reprend sa place, seul.

**La déduplication se dit** (ADR-014, ADR-041). `POST …/documents/ingest` renvoie le document
**existant** quand l'empreinte est déjà connue, sans que `created` n'atteigne le client : la file
le déduit du nom renvoyé et des identifiants connus **avant** l'envoi, et l'annonce plutôt que de
laisser l'utilisateur chercher un fichier qui n'a jamais été créé.

**Polling adaptatif, arrêté aux points d'arrêt.** `queued`/`prechecking` ~1,2 s ;
`chunking`/`embedding`/`indexing` ~2,5 s ; `awaiting_review` et états terminaux : **aucune
requête**. Le drapeau `resuming` rallume brièvement le polling après la validation, sinon l'écran
resterait figé pendant la reprise asynchrone du job (`api/jobs.ts`).

**Une ligne = un fichier = un job.** Un refus (format, quota, droits) n'interrompt aucune autre
ligne de la file. Deux envois simultanés au plus : le pipeline en aval est étroit (queue GPU à
concurrence 1, CONTRIBUTING.md §2), en lâcher dix n'irait pas plus vite.

**Les cases d'aiguillage sont pré-cochées d'après l'analyse** (tableaux détectés ⇒ découpage
tabulaire, document numérisé ⇒ OCR lourd), puis appartiennent à l'utilisateur. Le rapport est un
signal d'aide à la décision, pas un verdict : chaque constat porte le chiffre qui le fonde, pour
qu'on puisse le contredire en connaissance de cause.

**L'annulation n'est pas « tout état non terminal ».** `DELETE /jobs/{id}` n'accepte que
`queued` et `awaiting_review` (`CANCELLABLE_STATES`, `backend/app/domain/state_machine.py`) ;
`prechecking`/`chunking`/`embedding`/`indexing` répondent `409` à coup sûr, pas par exception de
course. `isCancellableJobState` (`api/jobs.ts`) mirrore cette règle serveur pour n'afficher le
bouton d'annulation que là où il peut réellement aboutir — un `409` reste géré en retour (l'état
a pu changer entre le rendu et le clic), mais ce n'est pas le cas nominal.
