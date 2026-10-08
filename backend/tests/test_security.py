"""Primitives cryptographiques : mots de passe et jetons opaques.

Ces tests ne touchent ni la base ni FastAPI : ils restent verts sans PostgreSQL,
et ce sont eux qui garantissent qu'un mot de passe ne sera jamais réduit à un
simple SHA-256.
"""

from __future__ import annotations

import hashlib

import pytest

from app.core.security import (
    API_KEY_PREFIX_LENGTH,
    generate_api_key_token,
    generate_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    token_prefix,
    verify_password,
)

PASSWORD = "correct horse battery staple"


def test_password_hash_is_argon2id() -> None:
    """L'algorithme est lisible dans l'empreinte : aucune ambiguïté au support."""
    assert hash_password(PASSWORD).startswith("$argon2id$")


def test_password_hash_never_contains_the_password() -> None:
    """Une empreinte n'est pas un encodage : le clair n'y apparaît pas."""
    assert PASSWORD not in hash_password(PASSWORD)


def test_two_hashes_of_the_same_password_differ() -> None:
    """Le sel est aléatoire : deux comptes partageant un mot de passe ne se trahissent pas."""
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_a_password_is_not_hashed_like_a_token() -> None:
    """Garde-fou explicite : `sha256(mot_de_passe)` serait cassable par force brute."""
    assert hash_password(PASSWORD) != hashlib.sha256(PASSWORD.encode()).hexdigest()


def test_verify_password_accepts_the_right_password() -> None:
    """Le cas nominal : l'empreinte se relit."""
    assert verify_password(PASSWORD, hash_password(PASSWORD)) is True


@pytest.mark.parametrize("candidate", ["", "wrong password", PASSWORD.upper(), PASSWORD + " "])
def test_verify_password_refuses_anything_else(candidate: str) -> None:
    """Casse, espace final ou chaîne vide : rien d'autre ne passe."""
    assert verify_password(candidate, hash_password(PASSWORD)) is False


@pytest.mark.parametrize("stored", ["", "pas-une-empreinte", "$argon2id$tronqué"])
def test_verify_password_returns_false_on_a_corrupt_hash(stored: str) -> None:
    """Une empreinte illisible en base est un échec de connexion, pas une 500."""
    assert verify_password(PASSWORD, stored) is False


def test_a_fresh_hash_does_not_need_rehashing() -> None:
    """Les paramètres courants n'exigent évidemment pas de re-dérivation."""
    assert password_needs_rehash(hash_password(PASSWORD)) is False


def test_generated_tokens_are_high_entropy_and_unique() -> None:
    """256 bits par jeton : la collision et la force brute sont hors de portée."""
    tokens = {generate_token() for _ in range(256)}

    assert len(tokens) == 256
    assert all(len(token) == 43 for token in tokens)


def test_a_token_is_url_safe() -> None:
    """Le jeton voyage en cookie et en en-tête : aucun caractère à échapper."""
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

    assert set(generate_token()) <= allowed


def test_hash_token_is_deterministic_sha256() -> None:
    """Déterministe et sans sel : c'est ce qui permet la recherche par index."""
    token = generate_token()

    assert hash_token(token) == hashlib.sha256(token.encode()).hexdigest()
    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64


def test_api_key_token_follows_the_documented_format() -> None:
    """`rag_<env>_<43 caractères>` (`docs/domain-model.md` §1)."""
    token = generate_api_key_token("test")
    namespace, environment, secret = token.split("_", 2)

    assert (namespace, environment) == ("rag", "test")
    assert len(secret) == 43


def test_api_key_prefix_is_twelve_characters() -> None:
    """Le préfixe stocké en clair sert à reconnaître une clé, pas à s'authentifier."""
    token = generate_api_key_token("test")

    assert token_prefix(token) == token[:API_KEY_PREFIX_LENGTH]
    assert len(token_prefix(token)) == API_KEY_PREFIX_LENGTH
