"""pywa_async client factory with a per-business TTL cache.

The cache stores the constructed `WhatsApp` client, not the raw token —
decryption happens inside this module and the plaintext never escapes the
local variable scope. On cache miss we re-read the WaCredential row, decrypt,
and rebuild.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional
from uuid import UUID

from pywa_async import WhatsApp

from app.core.database import engine
from app.core.token_crypto import decrypt_token
from app.repositories.whatsapp_repo import get_credentials
from sqlmodel import Session

logger = logging.getLogger(__name__)


class WhatsAppNotConnected(RuntimeError):
    """Raised when no usable WaCredential exists for a business — either no row,
    token_status != 'active', or the stored ciphertext can't be decrypted."""


_TTL_SECONDS = 3600  # 1 hour
_cache: dict[str, tuple[WhatsApp, float]] = {}
_cache_lock = threading.Lock()


def get_client(business_id: UUID) -> WhatsApp:
    key = str(business_id)
    now = time.monotonic()

    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            client, expires_at = cached
            if expires_at > now:
                return client
            # expired — fall through to rebuild
            _cache.pop(key, None)

    client = _build_client(business_id)

    with _cache_lock:
        _cache[key] = (client, now + _TTL_SECONDS)
    return client


def invalidate(business_id: UUID) -> None:
    """Drop the cached client — call this when the credential is rotated or
    revoked so the next send rebuilds with a fresh token."""
    with _cache_lock:
        _cache.pop(str(business_id), None)


def _build_client(business_id: UUID) -> WhatsApp:
    with Session(engine) as session:
        cred = get_credentials(session, business_id)
        if cred is None:
            raise WhatsAppNotConnected(
                f"No WaCredential row for business_id={business_id}"
            )
        if cred.token_status != "active":
            raise WhatsAppNotConnected(
                f"WaCredential for business_id={business_id} has "
                f"token_status={cred.token_status!r}; must be 'active'"
            )
        phone_id = cred.phone_number_id
        token = decrypt_token(cred.access_token_enc)  # plaintext lives only on this stack

    # Token passed positionally so it never lands in a kwargs dict we might log.
    return WhatsApp(phone_id=phone_id, token=token)
