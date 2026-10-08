"""Stockage local : écriture, relecture, adressage par empreinte, confinement.

Ce que ces tests verrouillent : **un même contenu reversé deux fois n'occupe
qu'un emplacement** — c'est ce qui aligne le stockage sur l'idempotence de
`DocumentService.register` — et **un `storage_uri` altéré ne devient jamais une
lecture de fichier arbitraire**.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.core.config import Settings
from app.providers.storage import LocalFilesystemStorageAdapter, StoragePort, resolve_storage
from app.providers.storage.base import StorageError

CONTENT = b"%PDF-1.4 rapport trimestriel"
CHECKSUM = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def storage(tmp_path: Path) -> LocalFilesystemStorageAdapter:
    """Adaptateur branché sur une racine jetable."""
    return LocalFilesystemStorageAdapter(root=tmp_path / "documents")


async def test_a_saved_document_is_read_back_byte_for_byte(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Aller-retour complet : c'est le contrat minimal du port."""
    uri = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="rapport.pdf")

    assert await storage.read(uri) == CONTENT


async def test_the_path_is_derived_from_the_checksum(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """`{racine}/{empreinte[:2]}/{empreinte}{extension}` — pas d'UUID d'upload."""
    uri = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="rapport.pdf")

    expected = storage.root / CHECKSUM[:2] / f"{CHECKSUM}.pdf"
    assert expected.is_file()
    assert expected.as_posix() in uri


async def test_the_extension_of_the_original_name_is_kept(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Le fichier reste identifiable sur disque, sans que le nom porte l'identité."""
    uri = await storage.save(b"a;b\n1;2\n", checksum_sha256="ab" * 32, filename="export.CSV")

    assert uri.endswith(".CSV")


async def test_a_name_without_extension_is_accepted(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """L'extension est un confort, jamais une condition d'écriture."""
    uri = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="rapport")

    assert await storage.read(uri) == CONTENT


async def test_saving_the_same_content_twice_writes_one_single_file(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Idempotence par empreinte : deux téléversements, un seul objet sur disque."""
    first = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="rapport.pdf")
    second = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="rapport.pdf")

    assert first == second
    assert len(list((storage.root / CHECKSUM[:2]).iterdir())) == 1


async def test_two_names_for_the_same_content_share_the_same_object(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """L'identité est le contenu, pas l'étiquette : seule l'extension peut varier."""
    first = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="a.pdf")
    second = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="b.pdf")

    assert first == second


async def test_no_partial_file_survives_a_write(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Écriture atomique : aucun résidu temporaire ne reste visible après coup."""
    await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="rapport.pdf")

    residues = [path.name for path in storage.root.rglob("*.tmp")]
    assert residues == []


async def test_reading_an_unknown_uri_raises_a_storage_error(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Un document dont les octets ont disparu est une erreur de déploiement, pas un 404."""
    missing = f"file://{(storage.root / 'ab' / 'absent.pdf').as_posix()}"

    with pytest.raises(StorageError):
        await storage.read(missing)


async def test_reading_outside_the_root_is_refused(
    storage: LocalFilesystemStorageAdapter, tmp_path: Path
) -> None:
    """Un URI altéré ne doit jamais transformer `read` en lecture arbitraire."""
    outsider = tmp_path / "secret.txt"
    outsider.write_bytes(b"hors perimetre")

    with pytest.raises(StorageError, match="hors du périmètre"):
        await storage.read(f"file://{outsider.as_posix()}")


async def test_an_unknown_uri_scheme_is_refused(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Un URI d'un autre backend ne doit pas être interprété comme un chemin local."""
    with pytest.raises(StorageError, match="schéma attendu"):
        await storage.read("s3://bucket/objet.pdf")


async def test_deleting_removes_the_bytes(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Après effacement, la relecture échoue : c'est le seul constat qui compte."""
    uri = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="rapport.pdf")

    await storage.delete(uri)

    with pytest.raises(StorageError):
        await storage.read(uri)


async def test_deleting_twice_is_not_an_error(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Idempotence : la suppression d'un document est rejouable après une interruption."""
    uri = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="rapport.pdf")

    await storage.delete(uri)
    await storage.delete(uri)


async def test_deleting_outside_the_root_is_refused(
    storage: LocalFilesystemStorageAdapter, tmp_path: Path
) -> None:
    """Le confinement vaut à l'effacement autant qu'à la lecture — et le fichier survit."""
    outsider = tmp_path / "secret.txt"
    outsider.write_bytes(b"hors perimetre")

    with pytest.raises(StorageError, match="hors du périmètre"):
        await storage.delete(f"file://{outsider.as_posix()}")

    assert outsider.is_file()


async def test_a_name_carrying_a_path_cannot_escape_the_root(
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Le nom ne compose jamais le chemin : seule l'empreinte l'adresse."""
    uri = await storage.save(CONTENT, checksum_sha256=CHECKSUM, filename="../../etc/passwd")

    assert Path(uri.removeprefix("file://")).is_relative_to(storage.root)


def test_the_resolved_storage_honours_the_configured_root(tmp_path: Path) -> None:
    """`RAGENGINE_STORAGE_ROOT_DIR` est la seule source de la racine (aucun chemin en dur)."""
    resolved = resolve_storage(Settings(storage_root_dir=tmp_path / "ailleurs"))

    assert isinstance(resolved, LocalFilesystemStorageAdapter)
    assert resolved.root == (tmp_path / "ailleurs").resolve()


def test_the_adapter_satisfies_the_port(tmp_path: Path) -> None:
    """La vérification statique fait foi ; celle-ci verrouille la forme à l'exécution."""
    assert isinstance(LocalFilesystemStorageAdapter(root=tmp_path), StoragePort)


def test_building_the_adapter_touches_no_disk(tmp_path: Path) -> None:
    """Inspecter la configuration ne doit jamais laisser un répertoire derrière soi."""
    root = tmp_path / "jamais-cree"

    LocalFilesystemStorageAdapter(root=root)

    assert not root.exists()
