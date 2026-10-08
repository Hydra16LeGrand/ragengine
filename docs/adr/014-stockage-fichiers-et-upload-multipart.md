# ADR-014 — Stockage des fichiers téléversés et bascule vers l'upload multipart

**Date :** 2026-07-27 · **Statut :** accepté

## Résumé (lire le corps si tu touches un de ces points)

- `POST .../documents/ingest` passe en `multipart/form-data` ; `content_type`,
  `size_bytes`, `checksum_sha256` sont **calculés côté serveur**, jamais repris du client.
- Nouveau port `StoragePort` (`save`/`read`), une seule implémentation :
  `LocalFilesystemStorageAdapter`, adressage par empreinte SHA-256 (idempotent par construction).
- Pas de registre multi-implémentation (comme `IngestionEnginePort`) : un seul backend existe,
  à revoir en registre seulement si un second (MinIO/S3) devient réel.
- Volume Docker nommé partagé entre `api` (écrit) et `worker-default` (relit) — c'est ce
  partage, pas un état en mémoire, qui permet à un job repris après crash de retrouver ses octets.
- Aucun chiffrement au repos en v1 (hors périmètre, cf. ADR-006 si ça change).

## Contexte

La Phase 1 a délibérément laissé `POST /api/v1/knowledge-bases/{kb_id}/documents/ingest`
accepter un corps JSON (`DocumentCreate` : `filename`, `content_type`,
`size_bytes`, `checksum_sha256`, `storage_uri`) plutôt qu'un fichier réel — le
contrat d'API (`202` + job à suivre) était fixé sans qu'aucun octet ne soit
manipulé (`app/services/documents.py`, docstring d'origine : « le contrat d'API
est déjà le bon : seule la source des métadonnées changera »).

La Phase 4 rend cette étape inévitable : `run_precheck` doit lire le fichier
pour l'analyser (`app/ingestion/precheck/`), et `run_chunking` doit en envoyer
les octets à RAGFlow (`IngestionEnginePort.upload_document`). Aucun des deux
n'existe tant que RAGEngine ne possède pas réellement le fichier quelque part.

## Décision

1. **`POST /api/v1/knowledge-bases/{kb_id}/documents/ingest` bascule en
   `multipart/form-data`** (champ fichier + champ `filename` optionnel). Les
   métadonnées jusqu'ici prises telles quelles dans le corps JSON
   (`content_type`, `size_bytes`, `checksum_sha256`) sont désormais **calculées
   côté serveur** à partir des octets reçus — jamais reprises d'une valeur
   client. Le contrat de réponse (`202`, `DocumentRead`, `current_job_id` à
   suivre) ne change pas. `DocumentCreate` (`app/domain/schemas/document.py`)
   est retiré du corps public de cet endpoint ; l'idempotence par
   `checksum_sha256` (`DocumentService.register`) est inchangée, calculée
   maintenant sur le flux reçu plutôt que déclarée par le client.

2. **Nouveau port `StoragePort`** (`app/providers/storage/base.py`), au même
   principe que les autres ports substituables (CONTRIBUTING.md §3) : le domaine ne
   connaît que l'interface, jamais le système de fichiers.

   ```python
   class StoragePort(Protocol):
       async def save(self, content: bytes, *, checksum_sha256: str, filename: str) -> str:
           """Persiste le contenu, renvoie le `storage_uri` opaque à stocker sur `Document`."""
           ...

       async def read(self, storage_uri: str) -> bytes:
           """Relit le contenu depuis son `storage_uri`."""
           ...
   ```

   Un seul adaptateur en v1 : `LocalFilesystemStorageAdapter`
   (`app/providers/storage/local.py`), qui écrit sous un répertoire racine
   configurable (nouveau réglage, `RAGENGINE_STORAGE_ROOT_DIR`, défaut
   `/data/documents` dans le conteneur). Comme pour `IngestionEnginePort`
   (RAGFlow, ADR-001) : une seule implémentation existe aujourd'hui, donc pas de
   registre/factory à la manière d'`embeddings`/`rerankers`/`llm` — une
   fonction de résolution directe suffit (`app/providers/storage/__init__.py`
   ou colocalisée dans les dépendances FastAPI), à étendre en registre le jour
   où un second backend (MinIO/S3) est réellement envisagé, pas avant.

   **Adressage par empreinte** : le chemin sur disque est dérivé de
   `checksum_sha256` (ex. `{root}/{checksum[:2]}/{checksum}{extension}`), pas
   d'un UUID généré à l'upload. Un même fichier reversé deux fois écrit au même
   chemin — cohérent avec l'idempotence déjà en place côté `DocumentService`,
   et rend un job repris après crash capable de relire le fichier sans jamais
   dépendre d'un état en mémoire du process qui a reçu l'upload.

3. **Volume Docker partagé.** `docker/docker-compose.yml` gagne un volume
   nommé (`ragengine_documents_data`), monté au même chemin
   (`RAGENGINE_STORAGE_ROOT_DIR`) sur `api` (écrit à l'upload) et
   `worker-default` (lu par `run_precheck` et `run_chunking`) — les deux seuls
   services qui touchent des octets de documents. Pas de volume sur les
   services RAGFlow : celui-ci reçoit son propre exemplaire via
   `upload_document`, RAGEngine ne partage jamais son stockage brut avec un
   tiers.

## Conséquences

- `DocumentCreate` perd `content_type`, `size_bytes`, `checksum_sha256`,
  `storage_uri` de son usage en entrée d'API (le modèle de lecture
  `DocumentRead` ne change pas) ; ces valeurs sont calculées dans
  `DocumentService.register` à partir des octets reçus par le router.
- `app/api/v1/routes/documents.py` (`ingest_document`) prend désormais un
  `UploadFile` (FastAPI) au lieu d'un `DocumentCreate` complet.
- Toute doc/exemple d'API antérieur montrant un corps JSON pour cet endpoint
  est obsolète et doit être corrigé dans la même passe.
- Un fichier local n'est pas chiffré au repos en v1 — hors périmètre de cette
  ADR, à revisiter si un déploiement partagé l'exige (cf. `docs/adr/006`).
