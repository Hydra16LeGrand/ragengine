"""Briques partagées des schémas d'entrée/sortie.

Deux bases distinctes plutôt qu'une seule : un schéma d'**entrée** refuse les
champs inconnus (une faute de frappe du client doit produire un 422, pas être
ignorée en silence), tandis qu'un schéma de **sortie** se construit depuis un
objet ORM.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, SecretStr

from app.domain.slug import SLUG_MAX_LENGTH, SLUG_PATTERN

MIN_PASSWORD_LENGTH: Final = 8
MAX_PASSWORD_LENGTH: Final = 256
MAX_DISPLAY_NAME_LENGTH: Final = 120
MAX_NAME_LENGTH: Final = 200
MAX_DESCRIPTION_LENGTH: Final = 2_000
MAX_THEME_LENGTH: Final = 200
"""Étiquette de thème d'un document : un libellé court, comme un nom de ressource."""
MAX_FILENAME_LENGTH: Final = 255
"""`NAME_MAX` des systèmes de fichiers courants : aucun nom de fichier réel ne le dépasse."""
MAX_PROVIDER_ID_LENGTH: Final = 200
"""Identifiant de modèle ou de fournisseur, résolu contre un catalogue fermé."""
SHA256_HEX_PATTERN: Final = r"^[0-9a-f]{64}$"


class InputModel(BaseModel):
    """Base des schémas reçus du client."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReadModel(BaseModel):
    """Base des schémas renvoyés au client, construits depuis l'ORM.

    Aucun schéma dérivé ne porte de secret : `password_hash`, `key_hash` et
    `token_hash` ne sortent jamais de la couche de persistance.
    """

    model_config = ConfigDict(from_attributes=True)


def _lowercase_email(value: object) -> object:
    """Normalise l'adresse en minuscules avant toute validation ou comparaison."""
    return value.strip().lower() if isinstance(value, str) else value


NormalizedEmail = Annotated[EmailStr, BeforeValidator(_lowercase_email)]
"""Adresse e-mail validée **et** normalisée en minuscules (unicité sur `lower(email)`)."""

Password = Annotated[
    SecretStr,
    Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH),
]
"""Mot de passe en clair, uniquement en entrée — jamais stocké ni renvoyé tel quel."""

Slug = Annotated[str, Field(pattern=SLUG_PATTERN, max_length=SLUG_MAX_LENGTH)]
"""Identifiant lisible, unique par propriétaire."""

DisplayName = Annotated[str, Field(min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)]
ResourceName = Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH)]
Description = Annotated[str, Field(max_length=MAX_DESCRIPTION_LENGTH)]
Sha256Hex = Annotated[str, Field(pattern=SHA256_HEX_PATTERN)]
