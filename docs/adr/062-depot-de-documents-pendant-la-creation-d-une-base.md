# ADR-062 — Dépôt de documents pendant la création d'une base

**Date :** 2026-09-17 · **Statut :** accepté · **Origine :** demande utilisateur, remontées d'usage

## Contexte

Le parcours imposait de créer une base puis, seulement ensuite, aller déposer des documents sur son
écran de détail — perçu comme un ordre artificiel. Aucun changement de contrat d'API n'était
justifié : création et ingestion document par document sont déjà deux appels séparés
(`POST /v1/knowledge-bases` puis `POST /v1/knowledge-bases/{kb_id}/documents/ingest`), et le doivent
rester (une ingestion ne peut structurellement pas précéder la création — elle a besoin de
l'identifiant renvoyé par celle-ci).

## Décision

Le formulaire de création (`KnowledgeBaseForm`, mode `create`) porte une section Documents : une
`Dropzone` et une liste en attente (`StagedFileRow`, extrait d'`UploadPanel` pour être partagé plutôt
que dupliqué). Ces fichiers sont purement locaux — aucun appel réseau — jusqu'à la validation.

Au clic sur « Créer la base » : `POST /knowledge-bases` d'abord. Échec ⇒ rien d'autre ne se passe,
les fichiers restent en attente sur l'écran, inchangés. Succès ⇒ les fichiers sont déposés dans un
relais **en mémoire, module-level** (`features/ingestion/pendingUploads.ts`, une `Map<kbId, File[]>`
à consommation unique) avant de naviguer immédiatement vers le détail de la base.

Sur cet écran, `UploadPanel` (déjà monté pour tout compte en écriture) reprend ces fichiers dès son
montage et les lance directement — sans repasser par un second « Lancer l'ingestion », l'utilisateur
a déjà validé une fois. La file, la progression par fichier et la reprise d'un envoi en échec sont
donc celles qu'`UploadPanel` porte déjà pour tout versement ordinaire, pas reconstruites pour ce seul
parcours.

**Écarté : faire transiter les `File` par l'état de navigation** (`navigate(path, { state })`).
Réellement possible — `File` est structurellement clonable, et `history.pushState` (sous
`BrowserRouter`) l'accepterait — mais ce pari sur le clonage structuré du navigateur, pour un trajet
qui ne quitte jamais l'onglet courant, n'apportait rien qu'un simple module JS ne fasse plus
simplement et plus prévisiblement.

**Écarté : bloquer l'écran de création jusqu'à la fin des envois.** Cela aurait dupliqué la file,
la progression et la reprise déjà présentes dans `UploadPanel` pour un seul bénéfice — voir
l'enchaînement sans changer d'écran. La navigation immédiate après création (`UploadPanel` affichant
sa file dès son montage) donne une progression tout aussi visible, sans reconstruire cette mécanique
une seconde fois.

## Conséquences

- Aucune régression sur le versement depuis l'écran de détail : `UploadPanel` est inchangé dans son
  comportement, seul un effet de reprise au montage s'y ajoute (gardé par une référence contre le
  double montage de `StrictMode`, cf. commentaire dans `UploadPanel.tsx`).
- Un échec de création n'envoie jamais rien ; un échec d'envoi après une création réussie n'efface
  jamais la base, et se répare avec le bouton de reprise déjà existant.
- Tests : `KnowledgeBaseCreatePage.test.tsx` (ordre création → envoi, échec de création sans envoi,
  échec d'envoi avec reprise réussie), tous trois montés avec le vrai `UploadPanel` sur une sonde de
  route `/knowledge-bases/:kbId` — pas une simulation de son comportement.
