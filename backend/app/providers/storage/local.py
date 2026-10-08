"""Adaptateur `StoragePort` sur le système de fichiers local (ADR-014).

Seule implémentation de la v1. Le déploiement lui donne un volume Docker nommé,
monté au même chemin sur la façade (qui écrit à l'upload) et sur le worker
`default` (qui relit au precheck et au chunking) : c'est ce partage, et non un
état en mémoire du process qui a reçu le fichier, qui permet à un job repris
après un crash de retrouver ses octets.

Trois décisions d'implémentation, chacune pour une raison précise :

- **Adressage par empreinte** (`{racine}/{sha256[:2]}/{sha256}{extension}`)
  plutôt que par UUID généré à l'upload. Un même fichier reversé deux fois écrit
  au même endroit, ce qui aligne le stockage sur l'idempotence déjà tenue par
  `DocumentService.register`. Le préfixe de deux caractères évite un répertoire
  unique de dizaines de milliers d'entrées.
- **Écriture atomique** : un fichier temporaire dans le répertoire de
  destination, puis `os.replace`. Un crash en cours d'écriture laisse au pire un
  résidu `.tmp`, jamais un fichier tronqué portant le nom d'une empreinte —
  c'est-à-dire jamais un contenu corrompu qu'un `read` ultérieur croirait bon.
- **Confinement vérifié à la lecture** : un `storage_uri` qui désignerait un
  chemin hors de la racine est refusé. Le champ vient de la base, donc d'une
  valeur que RAGEngine a écrite ; le vérifier tout de même coûte un `is_relative_to`
  et interdit qu'une donnée altérée transforme `read` en lecture arbitraire.

Tout le travail est bloquant (I/O disque) : il est déporté dans un thread, comme
toute opération synchrone appelée depuis FastAPI (CONTRIBUTING.md §6).
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import unquote, urlparse
from urllib.request import pathname2url

import anyio.to_thread

from app.core.config import Settings
from app.providers.storage.base import StorageError

URI_SCHEME: Final = "file"
"""Schéma des `storage_uri` produits — un chemin absolu, lisible et inspectable."""

FANOUT_LENGTH: Final = 2
"""Longueur du préfixe de répertoire dérivé de l'empreinte."""

_TEMPORARY_SUFFIX: Final = ".tmp"


def _extension_of(filename: str) -> str:
    """Extension d'origine, conservée pour que le fichier reste identifiable sur disque.

    `PurePosixPath` et non `Path` : le nom vient d'un client, il ne doit jamais
    être interprété selon les règles de chemin du serveur.
    """
    return PurePosixPath(filename).suffix


class LocalFilesystemStorageAdapter:
    """Stockage des documents sous un répertoire racine, adressé par empreinte."""

    def __init__(self, *, root: Path) -> None:
        self._root = root.resolve() if root.is_absolute() else root.absolute().resolve()

    @classmethod
    def from_settings(cls, settings: Settings) -> LocalFilesystemStorageAdapter:
        """Construit l'adaptateur depuis la configuration (aucun chemin en dur)."""
        return cls(root=settings.storage_root_dir)

    @property
    def root(self) -> Path:
        """Racine résolue du stockage — exposée pour les tests et le diagnostic."""
        return self._root

    async def save(self, content: bytes, *, checksum_sha256: str, filename: str) -> str:
        """Écrit le contenu à l'emplacement dérivé de son empreinte, et renvoie son URI."""
        destination = self._path_for(checksum_sha256, filename)
        await anyio.to_thread.run_sync(_write_atomically, destination, content)
        return self._uri_for(destination)

    async def read(self, storage_uri: str) -> bytes:
        """Relit le contenu désigné par `storage_uri`, après vérification du confinement."""
        path = self._path_of(storage_uri)
        try:
            return await anyio.to_thread.run_sync(path.read_bytes)
        except OSError as error:
            raise StorageError(f"Contenu illisible pour « {storage_uri} » : {error}.") from error

    async def delete(self, storage_uri: str) -> None:
        """Efface le fichier désigné, sans jamais échouer sur une absence.

        Le confinement est vérifié exactement comme à la lecture : un URI qui
        sortirait de la racine est refusé **avant** le moindre `unlink`, sinon
        une valeur altérée en base transformerait cette méthode en effacement
        arbitraire.

        Le répertoire de fanout (`{racine}/{empreinte[:2]}`) est laissé en
        place : il en existe au plus 256, il est partagé par tous les contenus
        de même préfixe, et tenter de le retirer ouvrirait une course avec une
        écriture concurrente qui vient d'y créer son temporaire.
        """
        path = self._path_of(storage_uri)
        try:
            await anyio.to_thread.run_sync(_unlink_if_present, path)
        except OSError as error:
            raise StorageError(f"Effacement impossible de « {storage_uri} » : {error}.") from error

    # ─── Traduction URI ↔ chemin ─────────────────────────────────────────────

    def _path_for(self, checksum_sha256: str, filename: str) -> Path:
        """Emplacement canonique d'un contenu : `{racine}/{empreinte[:2]}/{empreinte}{ext}`."""
        if not checksum_sha256:
            raise StorageError("Empreinte absente : impossible d'adresser le contenu.")
        prefix = checksum_sha256[:FANOUT_LENGTH]
        return self._root / prefix / f"{checksum_sha256}{_extension_of(filename)}"

    def _uri_for(self, path: Path) -> str:
        """URI opaque porté par `Document.storage_uri`."""
        return f"{URI_SCHEME}://{pathname2url(str(path))}"

    def _path_of(self, storage_uri: str) -> Path:
        """Chemin désigné par un URI, refusé s'il sort de la racine du stockage."""
        parsed = urlparse(storage_uri)
        if parsed.scheme != URI_SCHEME:
            message = (
                f"URI de stockage non pris en charge : « {storage_uri} » "
                f"(schéma attendu : {URI_SCHEME})."
            )
            raise StorageError(message)
        path = Path(unquote(parsed.path))
        if not path.is_absolute() or not path.is_relative_to(self._root):
            raise StorageError(f"URI de stockage hors du périmètre : « {storage_uri} ».")
        return path


def _unlink_if_present(path: Path) -> None:
    """Retire le fichier s'il existe — l'absence est le résultat attendu, pas une faute."""
    path.unlink(missing_ok=True)


def _write_atomically(destination: Path, content: bytes) -> None:
    """Écrit `content` sans jamais exposer un fichier partiel sous le nom final."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Le temporaire est créé dans le répertoire de destination : `os.replace`
        # n'est atomique qu'à l'intérieur d'un même système de fichiers.
        temporary = destination.with_name(f"{destination.name}.{os.getpid()}{_TEMPORARY_SUFFIX}")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    except OSError as error:
        raise StorageError(f"Écriture impossible dans le stockage : {error}.") from error
