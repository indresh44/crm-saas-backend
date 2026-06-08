"""Repository for the new Wa* WhatsApp ingest (wa_credentials, wa_messages).

Kept separate from the legacy whatsapp_*_repository.py modules so the new
hand-rolled webhook path has its own narrow surface.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.wa_credential import WaCredential
from app.models.wa_message import WaMessage


def find_business_by_phone_id(
    session: Session,
    phone_number_id: str,
) -> Optional[WaCredential]:
    """Resolve which business a Meta webhook delivery belongs to via the
    value.metadata.phone_number_id field. Returns the full credential row;
    callers read .business_id and .display_phone off it."""
    statement = select(WaCredential).where(
        WaCredential.phone_number_id == phone_number_id,
    )
    return session.exec(statement).first()


def message_exists(session: Session, wamid: str) -> bool:
    """Idempotency check — Meta retries failed deliveries, and a single
    delivery can include the same wamid more than once."""
    statement = select(WaMessage.id).where(WaMessage.wamid == wamid)
    return session.exec(statement).first() is not None


def insert_message(session: Session, message: WaMessage) -> WaMessage:
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def get_credentials(session: Session, business_id: UUID) -> Optional[WaCredential]:
    statement = select(WaCredential).where(WaCredential.business_id == business_id)
    return session.exec(statement).first()


def get_message_by_wamid(session: Session, wamid: str) -> Optional[WaMessage]:
    statement = select(WaMessage).where(WaMessage.wamid == wamid)
    return session.exec(statement).first()


def update_message_status(
    session: Session,
    wamid: str,
    new_status: str,
    error_detail: Optional[str] = None,
) -> Optional[WaMessage]:
    """Monotonic status update. Returns the updated row, or None if no row
    matches the wamid. Returns the existing row unchanged if the new status
    would regress (e.g. delivered arriving after read).

    Ordering: received < sent < delivered < read. `failed` is terminal and
    only overrides sent (not delivered/read, which prove the message
    actually got through — a stray failed callback is a Meta quirk)."""
    msg = get_message_by_wamid(session, wamid)
    if msg is None:
        return None

    if not _should_advance(msg.status, new_status):
        return msg

    msg.status = new_status
    if error_detail is not None:
        msg.error_detail = error_detail
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg


_STATUS_RANK = {"received": 0, "sent": 1, "delivered": 2, "read": 3}


def _should_advance(current: str, new: str) -> bool:
    if new == "failed":
        # failed only overrides earlier states; not delivered/read.
        return current in ("received", "sent")
    if current == "failed":
        return False
    cur_rank = _STATUS_RANK.get(current, -1)
    new_rank = _STATUS_RANK.get(new, -1)
    return new_rank > cur_rank
