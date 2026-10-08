"""Port `Storage` : contrat de persistance des octets d'un document téléversé.

Les services n'importent que ce module — jamais le système de fichiers, jamais
un SDK objet (ADR-014, CONTRIBUTING.md §3.1). Le port est un `Protocol` : un
adaptateur n'hérite de rien, il se contente d'exposer la bonne forme, ce qui
garde les implémentations (dont les fakes de test) libres de toute base commune.

Deux traits du contrat ne sont pas des choix esthétiques :

- **`storage_uri` est opaque.** `save` le fabrique, `read` sait seul le relire,
  et `Document.storage_uri` le transporte sans jamais l'interpréter. Un service
  qui reconstruirait un chemin à partir de ses morceaux ferait entrer la
  topologie du stockage dans le domaine, et la rendrait impossible à changer.
- **`checksum_sha256` est un paramètre, pas un calcul de l'adaptateur.**
  L'empreinte sert déjà l'idempotence de `DocumentService.register` : la
  recalculer ici ferait exister deux vérités pour la même valeur. C'est aussi
  elle qui adresse le contenu, ce qui rend `save` naturellement idempotent —
  reverser deux fois le même fichier écrit au même endroit.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import ClassVar, Protocol, runtime_checkable

from app.core.errors import RAGEngineError


class StorageError(RAGEngineError):
    """Échec de lecture ou d'écriture du stockage de documents.

    Aucun adaptateur ne laisse remonter une `OSError` brute : le service
    appelant ne connaît que cette erreur. Le statut est `500` car la faute est
    celle du déploiement (volume non monté, disque plein, fichier disparu du
    stockage alors que la ligne `Document` existe encore), jamais celle de la
    requête entrante.
    """

    status: ClassVar[int] = HTTPStatus.INTERNAL_SERVER_ERROR
    title: ClassVar[str] = "Internal Server Error"
    type: ClassVar[str] = "/problems/storage-error"


@runtime_checkable
class StoragePort(Protocol):
    """Contrat de stockage des fichiers d'origine (ADR-014).

    `runtime_checkable` sert aux assertions de test (`isinstance`) ; la
    vérification qui fait foi reste statique, via mypy, là où un adaptateur
    concret est fourni aux services et aux tâches.
    """

    async def save(self, content: bytes, *, checksum_sha256: str, filename: str) -> str:
        """Persiste le contenu et renvoie le `storage_uri` à porter sur `Document`.

        Idempotent : deux appels de même empreinte produisent le même
        emplacement et le même URI. `filename` ne sert qu'à conserver
        l'extension d'origine, jamais à composer l'identité de l'objet — deux
        noms différents pour un même contenu désignent le même fichier.

        Raises:
            StorageError: le contenu n'a pas pu être écrit.
        """
        ...

    async def read(self, storage_uri: str) -> bytes:
        """Relit le contenu depuis son `storage_uri`.

        Raises:
            StorageError: URI hors du périmètre du stockage, ou contenu
                introuvable/illisible.
        """
        ...

    async def delete(self, storage_uri: str) -> None:
        """Efface le contenu désigné par `storage_uri`.

        **Idempotent par contrat** : un contenu déjà absent est un succès, pas
        une erreur. C'est ce qui rend la suppression d'un document rejouable
        après une interruption — le seul appelant (`DocumentService.delete`) a
        déjà retiré la ligne `Document` quand il arrive ici, et n'aurait aucun
        moyen de réparer un échec sur un fichier qui n'existe plus.

        **L'appelant reste responsable du partage.** Le stockage adresse par
        empreinte : deux `Document` de bases différentes portant le même contenu
        désignent le **même** `storage_uri` (l'idempotence de
        `DocumentService.register` est scopée à une base). Un adaptateur ne peut
        pas connaître ce compte de références ; c'est au service de ne demander
        l'effacement que lorsque plus aucune ligne ne pointe sur l'URI.

        Raises:
            StorageError: URI hors du périmètre du stockage, ou effacement
                refusé par le système de fichiers.
        """
        ...
