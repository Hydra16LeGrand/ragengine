"""Dérivation des slugs — fonction pure, partagée par tous les appelants.

Un slug dérivé de deux manières différentes selon le module produirait des
identifiants incohérents pour un même nom : la règle vit ici, une seule fois.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

SLUG_PATTERN: Final = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
"""Forme canonique : minuscules, chiffres et tirets simples, sans tiret aux extrémités."""

SLUG_MAX_LENGTH: Final = 64

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_length: int = SLUG_MAX_LENGTH) -> str:
    """Convertit un nom libre en slug canonique.

    Les diacritiques sont décomposés puis retirés (« Référentiel Qualité » →
    `referentiel-qualite`) plutôt que remplacés par des tirets, pour rester
    lisible sur un corpus francophone.

    Renvoie une chaîne vide si le nom ne contient aucun caractère exploitable :
    c'est à l'appelant de décider du repli (identifiant technique, erreur 422).
    """
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return _NON_ALPHANUMERIC.sub("-", ascii_only).strip("-")[:max_length].strip("-")
