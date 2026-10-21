"""Application-layer authenticated encryption and one-way credential helpers."""

import base64
import hashlib
import hmac
import os
import secrets
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import Text, TypeDecorator

ENCRYPTED_PREFIX: Final = "enc:v1:"
FILE_MAGIC: Final = b"EAKA1"
_DEV_SECRET: Final = "local-development-only-do-not-use-in-production"


def _master_secret() -> bytes:
    value = os.getenv("ENCRYPTION_MASTER_KEY")
    if value:
        return value.encode()
    return _DEV_SECRET.encode()


def require_production_key() -> None:
    """Fail closed when a hosted account environment has no encryption key."""

    if os.getenv("ENCRYPTION_MASTER_KEY"):
        return
    app_env = os.getenv("APP_ENV", "development").lower()
    account_mode = os.getenv("ACCOUNT_AUTH_MODE", "").lower() in {"1", "true", "yes"}
    if account_mode or app_env not in {"development", "test", "testing"}:
        raise RuntimeError("ENCRYPTION_MASTER_KEY is required outside local development")


def _derive_key(purpose: bytes) -> bytes:
    return hmac.new(_master_secret(), b"enterprise-ai:" + purpose, hashlib.sha256).digest()


def encrypt_bytes(value: bytes, *, context: bytes) -> bytes:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_derive_key(b"aes-256-gcm")).encrypt(nonce, value, context)
    return FILE_MAGIC + nonce + ciphertext


def decrypt_bytes(value: bytes, *, context: bytes) -> bytes:
    if not value.startswith(FILE_MAGIC) or len(value) < len(FILE_MAGIC) + 13:
        raise ValueError("Encrypted payload header is invalid")
    offset = len(FILE_MAGIC)
    nonce = value[offset : offset + 12]
    ciphertext = value[offset + 12 :]
    return AESGCM(_derive_key(b"aes-256-gcm")).decrypt(nonce, ciphertext, context)


def encrypt_text(value: str) -> str:
    if value.startswith(ENCRYPTED_PREFIX):
        return value
    encrypted = encrypt_bytes(value.encode(), context=b"database-field:v1")
    return ENCRYPTED_PREFIX + base64.urlsafe_b64encode(encrypted).decode()


def decrypt_text(value: str) -> str:
    if not value.startswith(ENCRYPTED_PREFIX):
        # Backward-compatible read for data created before application encryption.
        return value
    payload = base64.urlsafe_b64decode(value.removeprefix(ENCRYPTED_PREFIX))
    return decrypt_bytes(payload, context=b"database-field:v1").decode()


def blind_index(value: str) -> str:
    normalized = value.strip().casefold().encode()
    return hmac.new(_derive_key(b"blind-index"), normalized, hashlib.sha256).hexdigest()


def content_fingerprint(value: bytes) -> str:
    """Keyed content digest used for duplicate detection without exposing a raw hash."""

    return hmac.new(_derive_key(b"content-fingerprint"), value, hashlib.sha256).hexdigest()


def password_hash(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    values = (
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(derived).decode(),
    )
    return "scrypt$32768$8$1$" + "$".join(values)


def password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, expected_value = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_value)
        expected = base64.urlsafe_b64decode(expected_value)
        actual = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hmac.new(_derive_key(b"token-hash"), token.encode(), hashlib.sha256).hexdigest()


class EncryptedText(TypeDecorator[str]):
    """Encrypt a text column before it reaches the database using AES-256-GCM."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        del dialect
        return encrypt_text(value) if value is not None else None

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        del dialect
        return decrypt_text(value) if value is not None else None
