"""Fernet-based symmetric encryption for sensitive secrets (Meta access tokens).

The key lives in WA_TOKEN_ENCRYPTION_KEY (urlsafe base64, 32 bytes). Never log
the plaintext token or the key. Fernet bundles AES-128-CBC + HMAC-SHA256 with
authenticated timestamps, which is sufficient for at-rest token storage where
the threat is DB compromise without app-server access.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class TokenEncryptionError(RuntimeError):
    """Raised when WA_TOKEN_ENCRYPTION_KEY is missing/malformed, or a ciphertext
    fails authentication. The message intentionally does not echo the secret."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.WA_TOKEN_ENCRYPTION_KEY
    if not key:
        raise TokenEncryptionError(
            "WA_TOKEN_ENCRYPTION_KEY is not set. Generate with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and put it in .env."
        )
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        raise TokenEncryptionError(
            "WA_TOKEN_ENCRYPTION_KEY is malformed; must be urlsafe base64 of 32 bytes."
        ) from e


def encrypt_token(plaintext: str) -> bytes:
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("plaintext token must be a non-empty string")
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_token(ciphertext: bytes) -> str:
    if not isinstance(ciphertext, (bytes, bytearray, memoryview)) or not ciphertext:
        raise ValueError("ciphertext must be non-empty bytes")
    try:
        return _fernet().decrypt(bytes(ciphertext)).decode("utf-8")
    except InvalidToken as e:
        raise TokenEncryptionError(
            "Failed to decrypt access token — either the WA_TOKEN_ENCRYPTION_KEY "
            "has changed since the token was stored, or the row was seeded with a "
            "placeholder."
        ) from e
