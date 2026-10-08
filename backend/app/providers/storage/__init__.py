"""Port `Storage` et son unique adaptateur (système de fichiers local, ADR-014).

**Pas de registre ici, et c'est délibéré.** `embeddings/`, `rerankers/` et
`llm/` exposent un registre parce qu'ils ont réellement plusieurs
implémentations à arbitrer par configuration. Le stockage n'en a qu'une : une
fonction de résolution directe suffit, à transformer en registre le jour où un
second backend (MinIO/S3) est réellement envisagé — pas avant (ADR-014).
"""

from __future__ import annotations

from app.core.config import Settings
from app.providers.storage.base import StorageError, StoragePort
from app.providers.storage.local import LocalFilesystemStorageAdapter


def resolve_storage(settings: Settings) -> StoragePort:
    """Renvoie l'adaptateur de stockage du déploiement.

    Construire l'adaptateur ne touche pas au disque : la racine n'est créée
    qu'à la première écriture, pour qu'inspecter la configuration ne laisse
    jamais de répertoire derrière soi.
    """
    return LocalFilesystemStorageAdapter.from_settings(settings)


__all__ = [
    "LocalFilesystemStorageAdapter",
    "StorageError",
    "StoragePort",
    "resolve_storage",
]
