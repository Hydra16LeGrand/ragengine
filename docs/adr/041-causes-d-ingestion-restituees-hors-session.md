# ADR-041 — Les causes d'ingestion sont restituées hors session de versement

**Date :** 2026-08-22 · **Statut :** accepté

## Contexte

La campagne de tests manuels du 2026-08 a produit plusieurs remontées de la forme
« l'ingestion a échoué sans possibilité de voir la cause ». Vérification faite
(l'analyse interne des tests manuels §3), la cause **était** enregistrée en base
(`ingestion_jobs.error`, `ingestion_jobs.warning`) et **était** affichée — mais par
`JobTracker` seul, c'est-à-dire pendant la session de versement uniquement. Quitter l'écran
ou le rafraîchir la rendait définitivement inatteignable : `DocumentsPanel` n'affichait
qu'un badge `failed`, sans code, sans message, sans moyen d'ouvrir le détail.
`26-pdf-blank-page.pdf` et `28-pdf-corrupted.pdf` portaient un `warning.code = zero_chunks`
que l'utilisateur n'a jamais pu lire.

Deux défauts adjacents, du même angle mort :

1. **Le message affiché était le dump brut du moteur.** Quatre échecs distincts du corpus
   avaient une seule et même cause — le quota gratuit du service de description d'images
   épuisé — rendue sous la forme
   `12:10:44 Page(1~4): [ERROR]429 RESOURCE_EXHAUSTED. {'error': {'code': 429, …`.
   Illisible, donc non relié : le quota avait été identifié pour un document et pas pour
   les trois autres.
2. **La déduplication était silencieuse.** `POST …/documents/ingest` est idempotent par
   empreinte (ADR-014) : un fichier déjà présent sous le même `checksum_sha256` ne crée ni
   ligne ni job, et le document **existant** est renvoyé. Comportement voulu et correct,
   mais jamais dit : l'utilisateur versant `29-pdf-duplicate-of-01.pdf` voyait « indexé »
   sans analyse préalable, puis ne trouvait aucune entrée à ce nom — c'est
   `01-pdf-single-column.pdf` qui lui avait été rendu. Vécu comme un bug.

## Décision

**1. La cause se consulte depuis la liste des documents, à la demande.** Un document
`failed` ou porteur d'un avertissement qualité gagne un dépli « Voir la cause » qui monte
`GET /api/v1/jobs/{current_job_id}` **au clic**, pas au rendu de la liste. Un corpus de
59 documents ne déclenche donc aucune requête supplémentaire tant que personne n'ouvre un
détail, et `DocumentRead` n'a pas eu à grossir : la liste continue de se rendre sans
jointure sur les jobs, contrat annoncé par le routeur. Le dépli plutôt qu'une boîte de
dialogue, pour que plusieurs échecs d'un même versement se comparent d'un coup d'œil.

**2. Le bloc d'échec est le même aux deux endroits.** `JobFailurePanel` et `QualityWarning`
sont sortis de `JobTracker` vers `features/ingestion/JobOutcome.tsx`, consommés à la fois
par le suivi de versement et par la liste. Un même défaut se lit donc pareil pendant et
après l'ingestion — dupliquer la mise en forme aurait garanti la divergence.

**3. La traduction des causes porte sur le message, pas sur le code.** Le moteur range trois
causes sans rapport (quota d'API épuisé, format non lu, image illisible) sous les mêmes
`engine_processing_failed` / `engine_error` : une table par code ne saurait pas les
distinguer. `failureCause.ts` éprouve donc des motifs sur le message brut, dans l'ordre de
fréquence réellement observée. Aucun motif ne correspond ⇒ **rien n'est inventé**, le message
brut reprend sa place d'origine, seul et déplié.

**4. Le message brut n'est jamais supprimé.** Quand une cause est reconnue, le constat et le
conseil passent devant, et le dump du moteur reste **complet** sous un repli. C'est lui qui
porte le diagnostic (page fautive, charge utile du fournisseur) : le remplacer par une phrase
lisible échangerait un défaut d'ergonomie contre une perte d'information.

**5. La déduplication est déduite côté client, sans toucher au contrat d'API.** Vérifié :
`created` **n'atteint pas le client**. Le service le calcule
(`RegisteredDocument.created`, `app/services/documents.py`), mais le routeur sérialise
`DocumentRead.model_validate(registered.document)` et le laisse tomber
(`app/api/v1/routes/documents.py`). Deux signaux le reconstituent sans supposition :

- le **nom renvoyé diffère du nom envoyé** — un document créé porte toujours le nom transmis
  (`resolve_filename` n'en retire que le chemin), donc un nom différent ne peut venir que
  d'une ligne préexistante ;
- l'**identifiant renvoyé figurait déjà** dans la base avant cet envoi — un identifiant
  fraîchement créé ne peut pas être dans une liste lue avant sa création.

La photographie des identifiants connus est prise **au départ** de l'envoi, jamais au retour :
entre les deux, l'aboutissement d'un autre versement invalide la liste, qui peut alors déjà
contenir le document que cet envoi-ci vient de créer — et un document neuf serait annoncé
comme un doublon. Le faux positif est ici plus grave que l'angle mort d'origine : il ferait
douter d'une indexation pourtant réussie.

Sur un doublon, l'interface nomme le document **réellement suivi**, pas le fichier déposé :
« 29-pdf-duplicate-of-01.pdf est interrogeable depuis le Playground » désignerait un document
qui n'existe nulle part.

## Conséquences

- Trois fichiers neufs côté frontend : `features/ingestion/failureCause.ts` (règles pures),
  `features/ingestion/JobOutcome.tsx` (rendu partagé), `features/ingestion/UploadPanel.test.tsx`.
  Aucun changement backend, aucun changement de `/api/v1`.
- Libellés ajoutés en FR **et** EN (`i18n/locales/`) : causes traduites, conseils attachés aux
  trois codes d'avertissement qualité (ADR-024), note de déduplication.
- Neuf tests Vitest ajoutés (23 → 32), tous posés sur la couture asynchrone : la cause
  n'apparaît qu'après l'aller-retour réseau déclenché par le dépli, et la déduplication ne se
  constate qu'en confrontant la requête à sa réponse — ni l'une ni l'autre n'est observable sur
  une fonction pure isolée (ADR-032 §5).
- **Angle mort résiduel, assumé.** Un fichier homonyme d'un document que la page n'a pas lu
  (au-delà de la première page de la liste) reste indétectable côté client. Le silence est
  alors celui d'avant — jamais une affirmation fausse. Le rendre étanche demanderait de faire
  remonter `created` : soit un en-tête de réponse sur `…/documents/ingest` (additif, hors
  schéma JSON, mais il faudrait exposer les en-têtes dans `api/client.ts`), soit un modèle de
  réponse dédié à l'ingestion. À arbitrer si le cas se présente réellement.
- **Piste non prise, hors mandat.** `POST /api/v1/jobs/{job_id}/retry` existe côté backend et
  n'est câblé nulle part dans l'interface. La cause la plus fréquente — le quota épuisé — est
  précisément celle qu'une reprise plus tard réglerait d'un clic. Le conseil affiché dit
  « réessayez plus tard » sans offrir le bouton qui le ferait.
