"""Pagination par curseur — jamais par offset.

Un `OFFSET` re-scanne les lignes sautées et fait sauter ou dupliquer des
éléments dès qu'une insertion survient pendant la navigation. Le curseur encode
la position exacte du dernier élément rendu, `(created_at, id)`, ce qui donne un
parcours stable et un coût constant.

`id` sert de départage : deux lignes créées dans la même transaction partagent
l'horodatage, et l'ordre serait alors indéterminé.

Le curseur est **opaque** pour le client (base64url) : sa structure interne
n'entre pas dans le contrat d'API et peut évoluer sans casser `/v1`. Un curseur
corrompu, tronqué ou forgé produit une `ValidationError` (422), jamais une 500.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Annotated, ClassVar, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import ValidationError

MIN_PAGE_SIZE: Final = 1
MAX_PAGE_SIZE: Final = 100
DEFAULT_PAGE_SIZE: Final = 50

_SEPARATOR: Final = "|"
_MALFORMED_CURSOR: Final = "Curseur de pagination illisible."


class CursorPaginable(Protocol):
    """Contrat d'une **classe** paginable : les colonnes servant au tri.

    Toute table héritant de `UUIDPrimaryKeyMixin` et `TimestampMixin` le
    satisfait sans déclaration supplémentaire — aucun modèle n'a à connaître ce
    protocole, c'est lui qui décrit le minimum dont la pagination a besoin.
    """

    id: ClassVar[InstrumentedAttribute[uuid.UUID]]
    created_at: ClassVar[InstrumentedAttribute[datetime]]


class CursorPosition(Protocol):
    """Contrat d'une **ligne chargée** : les valeurs, pas les colonnes.

    Distinct de `CursorPaginable` parce que `Mapped[T]` est un descripteur :
    lu sur la classe il donne une colonne, lu sur une instance il donne un `T`.
    """

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def created_at(self) -> datetime: ...


class Cursor(BaseModel):
    """Position de parcours : le couple `(created_at, id)` du dernier élément rendu."""

    model_config = ConfigDict(frozen=True)

    created_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        """Sérialise la position en base64url sans remplissage."""
        moment = self.created_at.astimezone(UTC).isoformat()
        raw = f"{moment}{_SEPARATOR}{self.id}".encode()
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, value: str) -> Cursor:
        """Relit un curseur opaque.

        Raises:
            ValidationError: curseur corrompu, tronqué ou forgé.
        """
        padded = value + "=" * (-len(value) % 4)
        try:
            raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            moment, separator, identifier = raw.partition(_SEPARATOR)
            if not separator:
                raise ValueError(_MALFORMED_CURSOR)
            created_at = datetime.fromisoformat(moment)
            parsed_id = uuid.UUID(identifier)
        except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError, ValueError) as exc:
            raise ValidationError(_MALFORMED_CURSOR) from exc
        if created_at.tzinfo is None:
            # Un horodatage sans fuseau est interprété en UTC, comme en base.
            created_at = created_at.replace(tzinfo=UTC)
        return cls(created_at=created_at, id=parsed_id)


class Page[ItemT](BaseModel):
    """Enveloppe de réponse paginée : `{"data": [...], "next_cursor": "..." | null}`."""

    data: list[ItemT] = Field(description="Éléments de la page, du plus récent au plus ancien.")
    next_cursor: str | None = Field(
        default=None,
        description="Curseur à repasser pour la page suivante ; `null` sur la dernière page.",
    )


class PaginationParams(BaseModel):
    """Paramètres de requête `?limit=&cursor=` à injecter dans les routers."""

    model_config = ConfigDict(frozen=True)

    limit: Annotated[int, Field(ge=MIN_PAGE_SIZE, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE
    cursor: str | None = None

    def decoded_cursor(self) -> Cursor | None:
        """Décode le curseur fourni, ou `None` s'il s'agit de la première page."""
        return Cursor.decode(self.cursor) if self.cursor else None


def _validated_limit(limit: int) -> int:
    """Refuse une taille de page hors bornes plutôt que de la rogner en silence."""
    if not MIN_PAGE_SIZE <= limit <= MAX_PAGE_SIZE:
        raise ValidationError(
            f"`limit` doit être compris entre {MIN_PAGE_SIZE} et {MAX_PAGE_SIZE}.",
            extensions={"limit": limit},
        )
    return limit


def apply_cursor_pagination[EntityT: CursorPaginable](
    statement: Select[tuple[EntityT]],
    entity: type[EntityT],
    *,
    cursor: str | Cursor | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> Select[tuple[EntityT]]:
    """Applique le tri, le filtre de curseur et la limite de garde à une requête.

    La requête demande volontairement `limit + 1` lignes : la présence de la
    ligne excédentaire indique qu'une page suivante existe, sans exécuter de
    `COUNT` sur toute la table. `build_page` coupe cette sentinelle.
    """
    checked_limit = _validated_limit(limit)
    position = Cursor.decode(cursor) if isinstance(cursor, str) else cursor
    if position is not None:
        statement = statement.where(
            or_(
                entity.created_at < position.created_at,
                and_(entity.created_at == position.created_at, entity.id < position.id),
            )
        )
    return statement.order_by(entity.created_at.desc(), entity.id.desc()).limit(checked_limit + 1)


def build_page[EntityT: CursorPosition, ItemT](
    rows: Sequence[EntityT],
    *,
    serializer: Callable[[EntityT], ItemT],
    limit: int = DEFAULT_PAGE_SIZE,
) -> Page[ItemT]:
    """Coupe la ligne sentinelle, sérialise la page et calcule `next_cursor`."""
    checked_limit = _validated_limit(limit)
    has_next = len(rows) > checked_limit
    visible = list(rows[:checked_limit])
    next_cursor = (
        Cursor(created_at=visible[-1].created_at, id=visible[-1].id).encode()
        if has_next and visible
        else None
    )
    return Page[ItemT](data=[serializer(row) for row in visible], next_cursor=next_cursor)
