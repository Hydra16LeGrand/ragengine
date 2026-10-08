"""Qui paie un appel LLM, et à quel compteur il est imputé (ADR-054, ADR-069).

Seul point de décision de la source de clé pour **toute** dépense LLM
déclenchée par un appelant : chat (`RetrievalService.chat`) et évaluation
(`EvaluationService`) — une seule table de vérité.

**Politique du cœur** (`LlmFundingPolicy`), complète et sûre sans greffon :

1. la clé personnelle de l'appelant pour le fournisseur demandé, s'il en a
   déclaré une (compte réel seulement) — le seul cas sans quota, et le seul où
   le modèle demandé est respecté ;
2. sinon la clé serveur `.env`, modèle forcé à `platform_llm_model_id` **s'il est
   renseigné** et appel imputé au compteur du compte (`QuotaSubject.USER`, ou
   `QuotaSubject.EVALUATION` pour une évaluation) puis au budget global — chacun
   seulement si son plafond est configuré (aucun par défaut dans le cœur).

Un appel hors clé personnelle exige donc un compte réel à qui l'imputer : sans
lui, `quota_scope` refuse (`401`/`403`) plutôt que de laisser partir une dépense
sans compteur. Un greffon peut remplacer la politique (`Plugin.llm_funding_policy`)
— par exemple pour financer un essai sans compte depuis une clé dédiée — en
sous-classant `LlmFundingPolicy`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ValidationError
from app.core.plugins import llm_funding_policy_factory
from app.providers.llm.registry import llm_provider_vendor
from app.services.identity.platform_llm_budget import LlmQuotaScope, QuotaSubject
from app.services.identity.principal import Principal
from app.services.identity.user_llm_keys import UserLlmKeyService


class LlmKeySource(StrEnum):
    """Sources de clé du cœur. Un greffon peut en ajouter (`LlmFunding.source` est un `str`)."""

    PERSONAL = "personal"
    """Clé LLM personnelle de l'appelant (BYOK) — seul cas sans quota."""
    SERVER = "server"
    """Clé serveur `.env`. Modèle forcé à `platform_llm_model_id` s'il est renseigné (addendum
    2026-09-21 à ADR-054) : le budget global compte des *appels*, à un coût supposé calibré pour ce
    modèle (`platform_llm_assumed_cost_per_message_usd`) — laisser l'appelant choisir un
    modèle plus cher sous ce même plafond de messages aurait consommé le quota pour une
    facture sans commune mesure."""


@dataclass(frozen=True, slots=True)
class LlmFunding:
    """Source de clé d'un appel, et le fournisseur qu'elle sert."""

    source: str
    provider_id: str
    override_api_key: str | None
    """Passée telle quelle à `resolve_llm_provider` : `None` = clé serveur `.env`."""

    @property
    def is_personal(self) -> bool:
        """`True` si l'appelant paie de sa propre clé : aucun quota ne s'applique alors."""
        return self.source == LlmKeySource.PERSONAL


class LlmFundingPolicy:
    """Politique du cœur — voir la docstring du module. Sous-classable par un greffon."""

    async def resolve(
        self, db: AsyncSession, settings: Settings, principal: Principal, provider_id: str
    ) -> LlmFunding:
        """Quelle clé paie : personnelle, sinon `shared_funding`.

        `provider_id` inconnu du catalogue (`llm_provider_vendor` retourne `None`)
        → `ValidationError` levée **ici**, avant toute autre décision : hors clé
        personnelle, `provider_id` est remplacé par `platform_llm_model_id` sans
        jamais retoucher `resolve_llm_provider` avec la valeur d'origine, ce qui
        laisserait sinon passer silencieusement un identifiant invalide.

        La clé personnelle n'est cherchée que pour un compte réel (session ou clé
        API — `principal.user` est alors le propriétaire de la clé), jamais pour
        un invité, et seulement pour le fournisseur précis demandé.
        """
        vendor = llm_provider_vendor(provider_id)
        if vendor is None:
            raise ValidationError(f"Modèle de génération « {provider_id} » inconnu.")

        if principal.user is not None and not principal.is_guest:
            personal_key = await UserLlmKeyService(db, settings).resolve_key(principal.user, vendor)
            if personal_key is not None:
                return LlmFunding(LlmKeySource.PERSONAL, provider_id, personal_key)
        return self.shared_funding(settings, provider_id)

    def shared_funding(self, settings: Settings, provider_id: str) -> LlmFunding:
        """Clé qui paie quand l'appelant n'a pas la sienne : la clé serveur.

        **Si `platform_llm_model_id` est renseigné, le modèle est forcé** (addendum
        2026-09-21 à ADR-054) : jamais un modèle coûteux financé par une clé qui n'est
        pas celle de l'appelant. Cœur seul, sans réglage : le modèle demandé est
        respecté (addendum 2026-10-04 : aucun plafond ni modèle imposé par défaut).
        """
        return LlmFunding(LlmKeySource.SERVER, settings.platform_llm_model_id or provider_id, None)

    def quota_scope(
        self, principal: Principal, *, via_evaluation: bool, client_ip: str | None
    ) -> LlmQuotaScope:
        """Compteur d'identité d'un appel hors clé personnelle.

        Réponse d'un item d'évaluation (`via_evaluation`, addendum 2026-09-21 à
        ADR-054) → le compteur **dédié** aux évaluations, jamais celui du chat ;
        sinon le compteur de chat du compte. `client_ip` ne sert pas au cœur : il
        est transmis pour une politique de greffon qui compterait par adresse.

        Raises:
            AuthenticationRequiredError: aucun compte à qui imputer l'appel.
            AccountRequiredError: espace éphémère, jamais financé par le serveur ici.
        """
        del client_ip
        user = principal.require_account()
        subject = QuotaSubject.EVALUATION if via_evaluation else QuotaSubject.USER
        return LlmQuotaScope(subject, str(user.id))


def get_llm_funding_policy() -> LlmFundingPolicy:
    """Politique active : celle d'un greffon s'il en fournit une, sinon celle du cœur."""
    factory = llm_funding_policy_factory()
    return factory() if factory is not None else LlmFundingPolicy()


async def resolve_llm_funding(
    db: AsyncSession, settings: Settings, principal: Principal, provider_id: str
) -> LlmFunding:
    """Raccourci : `get_llm_funding_policy().resolve(...)`."""
    return await get_llm_funding_policy().resolve(db, settings, principal, provider_id)
