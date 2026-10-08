"""Schémas d'entrée/sortie : normalisation, étanchéité aux secrets, strictesse."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from app.domain import schemas
from app.domain.enums import DocumentStatus, JobState, MemberRole, UserRole, Visibility
from app.domain.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    DocumentRead,
    IngestionJobRead,
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    PrecheckDecision,
    ProviderConfig,
    SessionCreate,
    SessionRead,
    UserCreate,
    UserRead,
    UserUpdate,
)

PASSWORD = "motdepasse-solide"

SECRET_FIELD_NAMES = frozenset({"password_hash", "key_hash", "token_hash", "password"})

READ_SCHEMAS: list[type[BaseModel]] = [
    UserRead,
    SessionRead,
    ApiKeyRead,
    KnowledgeBaseRead,
    schemas.KnowledgeBaseMemberRead,
    DocumentRead,
    IngestionJobRead,
    schemas.IngestionJobEventRead,
]


@pytest.mark.parametrize("schema", READ_SCHEMAS, ids=lambda schema: schema.__name__)
def test_read_schemas_never_expose_a_secret(schema: type[BaseModel]) -> None:
    """Aucune empreinte de mot de passe, de clé ou de jeton ne sort de l'API."""
    assert SECRET_FIELD_NAMES.isdisjoint(schema.model_fields)


def test_api_key_created_is_the_only_carrier_of_a_clear_token() -> None:
    """Le jeton n'existe qu'à la création, et n'est pas une empreinte."""
    assert "token" in ApiKeyCreated.model_fields
    assert "token" not in ApiKeyRead.model_fields
    assert SECRET_FIELD_NAMES.isdisjoint(ApiKeyCreated.model_fields)


@pytest.mark.parametrize(
    "raw",
    ["Alice@Example.COM", "  alice@example.com  ", "ALICE@EXAMPLE.COM"],
)
def test_email_is_normalized_to_lowercase(raw: str) -> None:
    """L'unicité portant sur `lower(email)`, la normalisation est faite en amont."""
    user = UserCreate(email=raw, password=SecretStr(PASSWORD), display_name="Alice")

    assert user.email == "alice@example.com"


def test_email_normalization_applies_to_the_login_schema() -> None:
    """La connexion normalise l'adresse comme l'inscription."""
    instance = SessionCreate(email="Bob@Example.COM", password=SecretStr(PASSWORD))

    assert instance.email == "bob@example.com"


def test_invalid_email_is_rejected() -> None:
    """Une adresse malformée ne franchit pas la frontière de l'API."""
    with pytest.raises(ValidationError):
        UserCreate(email="pas-une-adresse", password=SecretStr(PASSWORD), display_name="X")


def test_short_password_is_rejected() -> None:
    """La politique de longueur est portée par le schéma, pas par chaque router."""
    with pytest.raises(ValidationError):
        UserCreate(email="a@example.com", password=SecretStr("court"), display_name="X")


def test_password_is_a_secret_and_does_not_leak_in_repr() -> None:
    """Un log accidentel du schéma ne doit pas révéler le mot de passe."""
    user = UserCreate(email="a@example.com", password=SecretStr(PASSWORD), display_name="X")

    assert PASSWORD not in repr(user)
    assert user.password.get_secret_value() == PASSWORD


def test_input_schemas_reject_unknown_fields() -> None:
    """Une faute de frappe du client produit un 422, elle n'est pas ignorée."""
    with pytest.raises(ValidationError):
        UserUpdate(display_nme="Alice")  # type: ignore[call-arg]  # champ volontairement faux


def test_knowledge_base_create_defaults_are_conservative() -> None:
    """Une base est privée par défaut, briques optionnelles éteintes."""
    kb = KnowledgeBaseCreate(name="Référentiel Qualité", embedding_model_id="bge-m3")

    assert kb.visibility is Visibility.PRIVATE
    assert kb.observability_enabled is False
    assert kb.evaluation_enabled is False
    assert kb.slug is None
    assert kb.provider_config.model_dump() == {}


def test_provider_config_absorbs_unknown_options() -> None:
    """Le value object reste ouvert : la Phase 2 ajoute des options sans migration."""
    config = ProviderConfig.model_validate({"rerank_top_k": 8, "score_threshold": 0.35})

    assert config.model_dump() == {"rerank_top_k": 8, "score_threshold": 0.35}


@pytest.mark.parametrize("slug", ["ab_cd", "Majuscules", "-tiret-au-debut", "double--tiret"])
def test_invalid_slug_is_rejected(slug: str) -> None:
    """Le slug reste canonique : minuscules, chiffres et tirets simples."""
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(name="Base", embedding_model_id="bge-m3", slug=slug)


def test_the_precheck_decision_catalogue_is_closed() -> None:
    """ADR-014/§4 : quatre options, et un `422` sur toute autre — jamais un silence.

    `chunk_method_override` (Phase 15) rejoint le catalogue avec un défaut
    `None`, c'est-à-dire *aucune consigne* : le routage automatique reste
    entièrement inchangé pour un client qui ne l'envoie pas.
    """
    assert PrecheckDecision().model_dump() == {
        "optimize_for_tables": False,
        "heavy_ocr": False,
        "use_parent_child": False,
        "chunk_method_override": None,
    }

    with pytest.raises(ValidationError):
        PrecheckDecision.model_validate({"chunking_profile": "paper"})


def test_heavy_ocr_stays_opt_in_in_the_api_contract() -> None:
    """ADR-031 : l'interface propose `DeepDOC` sur les PDF, le contrat v1 ne bouge pas.

    Le 2026-08-11, `heavy_ocr` est passé de « proposé sur un scan probable » à
    « proposé sur tout PDF » — **côté interface uniquement**
    (`frontend/src/api/precheck.ts::suggestedDecision`). Ce défaut-ci reste
    `False` : un client REST qui omet le champ doit garder le comportement
    qu'il a toujours eu (routage par `is_likely_scanned`, ADR-023). Le passer à
    `True` serait une mutation silencieuse de `/v1` (CONTRIBUTING.md §4) — ce test
    est là pour que la tentation se solde par un échec, pas par un incident.
    """
    assert PrecheckDecision.model_validate({}).heavy_ocr is False
    assert PrecheckDecision.model_validate({"heavy_ocr": True}).heavy_ocr is True


def test_read_schema_is_built_from_an_orm_like_object() -> None:
    """`from_attributes` permet de sérialiser une ligne ORM sans dictionnaire pivot."""

    class Row:
        id = uuid.uuid4()
        email = "alice@example.com"
        display_name = "Alice"
        role = UserRole.ADMIN
        is_guest = False
        is_active = True
        expires_at = None
        last_login_at = None
        created_at = datetime.now(UTC)
        updated_at = datetime.now(UTC)
        password_hash = "$argon2id$secret"

    user = UserRead.model_validate(Row())

    assert user.role is UserRole.ADMIN
    assert "password_hash" not in user.model_dump()


def test_enums_serialize_as_their_string_values() -> None:
    """Les énumérations traversent JSON telles qu'elles sont écrites en base."""
    assert UserRole.ADMIN.value == "admin"
    assert MemberRole.OWNER.value == "owner"
    assert Visibility.PUBLIC.value == "public"
    assert DocumentStatus.AWAITING_REVIEW.value == "awaiting_review"
    assert JobState.CANCELLED.value == "cancelled"


def test_api_key_create_defaults_to_the_wildcard_scope() -> None:
    """La colonne `scopes` existe dès la v1 ; son application fine viendra en Phase 9."""
    assert ApiKeyCreate(name="CI").scopes == ["*"]
