"""Erreurs propres à l'identité, spécialisations du contrat RFC 9457 de `core.errors`.

Chaque sous-classe existe pour être **distinguable par le client** : l'interface
n'a pas à interpréter un message pour savoir s'il faut proposer la connexion,
l'élévation d'un espace invité, ou afficher un simple refus. Le `type` est donc
la partie stable du contrat, comme le prévoit la RFC 9457 §4.2.1.
"""

from __future__ import annotations

from typing import ClassVar

from app.core.errors import ForbiddenError, UnauthorizedError


class AuthenticationRequiredError(UnauthorizedError):
    """Action persistante tentée sans principal authentifié.

    Déclenche le parcours d'élévation côté interface (`docs/domain-model.md` §6).
    """

    title: ClassVar[str] = "Authentication Required"
    type: ClassVar[str] = "/problems/authentication-required"


class InvalidCredentialsError(UnauthorizedError):
    """Identifiant **présenté et rejeté** : mot de passe faux, clé API inconnue.

    Distinct d'`AuthenticationRequiredError` : ici le client a bien tenté de
    s'authentifier, et il doit corriger son identifiant plutôt que d'en demander
    un à l'utilisateur.
    """

    title: ClassVar[str] = "Invalid Credentials"
    type: ClassVar[str] = "/problems/invalid-credentials"


class InsufficientScopeError(ForbiddenError):
    """Clé API authentifiée mais dont les portées ne couvrent pas l'action.

    Distinct d'un simple `ForbiddenError` de la matrice §4 (rôle sur une base) :
    ici le principal a bien les droits **applicatifs**, c'est la clé elle-même
    qui a été émise avec un périmètre plus étroit (`app.domain.scopes`). La
    portée manquante voyage en extension : l'interface (ou un script) n'a pas à
    analyser un message pour savoir quelle portée ajouter à la clé.
    """

    title: ClassVar[str] = "Insufficient Scope"
    type: ClassVar[str] = "/problems/insufficient-scope"

    def __init__(self, required_scope: str, *, detail: str | None = None) -> None:
        super().__init__(
            detail or f"Cette clé API n'a pas la portée requise : « {required_scope} ».",
            extensions={"required_scope": required_scope},
        )
        self.required_scope = required_scope


class AccountRequiredError(ForbiddenError):
    """Action réservée aux comptes réels, tentée depuis un compte éphémère (`is_guest`).

    Le cœur ne crée jamais de compte éphémère : ce refus ne se produit que si un
    greffon en ouvre (ADR-069), qui offre alors lui-même le moyen de le réparer.
    """

    title: ClassVar[str] = "Account Required"
    type: ClassVar[str] = "/problems/account-required"
