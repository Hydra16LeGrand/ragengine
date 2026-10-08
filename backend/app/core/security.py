"""Primitives cryptographiques de l'authentification — sans I/O ni framework.

Trois familles de secrets cohabitent, et elles n'appellent **pas** le même
traitement :

- un **mot de passe** est choisi par un humain, donc de faible entropie : il
  exige une fonction de dérivation lente et salée (argon2id, ADR-005 et
  `docs/domain-model.md` §1). `hashlib.sha256(mot_de_passe)` serait cassable par
  force brute massivement parallèle ;
- un **jeton** (session, clé API) est tiré par le serveur avec 256 bits
  d'entropie : aucune force brute n'est envisageable, et un simple SHA-256
  suffit à ne pas stocker le clair. Le hachage doit d'ailleurs rester rapide,
  puisqu'il est calculé à **chaque** requête authentifiée.
- une **clé API tierce fournie par l'utilisateur** (Anthropic/OpenAI/Groq pour
  ses propres appels de génération) doit au contraire être **récupérable en
  clair** : RAGEngine l'envoie au fournisseur pour son compte, un hachage à
  sens unique la rendrait inutilisable. Chiffrement symétrique réversible
  (Fernet, `cryptography`), pas un KDF — c'est un besoin different du mot de
  passe et du jeton ci-dessus, à ne pas confondre.

Ce module ne connaît ni la base ni FastAPI : il est importable depuis un test
unitaire, une tâche Celery ou un script, sans configuration — les fonctions de
chiffrement prennent la clé de dérivation en paramètre plutôt que de lire
`Settings` elles-mêmes, pour la même raison.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

TOKEN_BYTES: Final = 32
"""Entropie d'un jeton opaque : 256 bits, soit 43 caractères base64url."""

API_KEY_PREFIX_LENGTH: Final = 12
"""Longueur du préfixe stocké en clair pour l'affichage et le support (§1)."""

_API_KEY_NAMESPACE: Final = "rag"
"""Racine du format de jeton `rag_<env>_<43 caractères base64url>` (§1)."""

_password_hasher: Final = PasswordHasher()
"""Paramètres par défaut d'`argon2-cffi` : argon2id, alignés sur la RFC 9106."""


def hash_password(password: str) -> str:
    """Dérive l'empreinte argon2id d'un mot de passe (sel aléatoire inclus)."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Vérifie un mot de passe contre son empreinte, sans jamais lever.

    `password_hash` vient d'une colonne `TEXT` : rien n'y garantit une empreinte
    bien formée. Une valeur tronquée, vide ou non-ASCII est donc traitée comme un
    échec de vérification — elle ne doit pas transformer la route de connexion en
    `500`, ce qui révélerait au passage l'état de la ligne visée.
    `UnicodeEncodeError` compte parmi ces cas : `argon2-cffi` encode l'empreinte
    en ASCII avant de l'analyser.
    """
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except (Argon2Error, ValueError):  # empreinte illisible (corruption, encodage)
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Indique si l'empreinte a été produite avec des paramètres dépassés.

    Permet de re-dériver silencieusement à la connexion suivante, le mot de
    passe en clair n'étant disponible qu'à cet instant.
    """
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except (Argon2Error, ValueError):  # pragma: no cover - empreinte illisible
        return False


def generate_token() -> str:
    """Tire un jeton opaque de 256 bits, encodé en base64url."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def generate_api_key_token(environment: str) -> str:
    """Compose un jeton de clé API au format `rag_<env>_<43 caractères>` (§1)."""
    return f"{_API_KEY_NAMESPACE}_{environment}_{generate_token()}"


def hash_token(token: str) -> str:
    """Empreinte SHA-256 hexadécimale d'un jeton, telle que stockée en base.

    Déterministe et sans sel — c'est ce qui permet de retrouver la ligne par
    index sur `token_hash` / `key_hash` en une seule requête.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    """Extrait le préfixe affichable d'un jeton de clé API."""
    return token[:API_KEY_PREFIX_LENGTH]


_FERNET_INFO: Final = b"ragengine-user-llm-key-v1"
"""Étiquette HKDF : change si jamais un second usage du chiffrement apparaît,
pour ne jamais réutiliser la même clé dérivée sur deux besoins différents."""


def encrypt_secret(plaintext: str, secret_key: str) -> str:
    """Chiffre une clé API tierce pour stockage en base (réversible, Fernet)."""
    fernet = Fernet(_fernet_key_bytes(secret_key))
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, secret_key: str) -> str:
    """Déchiffre une clé API tierce stockée en base.

    Raises:
        ValueError: jeton corrompu, ou chiffré avec un `secret_key` différent
            (rotation de secret sans migration des valeurs déjà chiffrées).
    """
    fernet = Fernet(_fernet_key_bytes(secret_key))
    try:
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        message = "Clé API tierce illisible : secret de chiffrement ou valeur corrompue."
        raise ValueError(message) from error


def _fernet_key_bytes(secret_key: str) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_FERNET_INFO)
    return base64.urlsafe_b64encode(hkdf.derive(secret_key.encode("utf-8")))
