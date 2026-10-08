"""Fixtures partagées des services.

Le stockage des fichiers est le seul port que les cas d'usage d'ingestion
touchent réellement (ADR-014). Il est fourni ici sous sa **vraie**
implémentation, branchée sur un répertoire jetable : les tests éprouvent donc le
chemin d'écriture réel, sans dépendre du volume Docker du déploiement ni de la
racine par défaut `/data/documents`, à laquelle une session de test n'a pas accès.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.storage import LocalFilesystemStorageAdapter


@pytest.fixture
def storage(tmp_path: Path) -> LocalFilesystemStorageAdapter:
    """Stockage local sur un répertoire propre au test, effacé avec lui."""
    return LocalFilesystemStorageAdapter(root=tmp_path / "documents")
