from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.whatsapp_account import WhatsAppAccountCreate, WhatsAppAccountRead
from app.services.whatsapp_account_service import (
    create_or_update_account,
    get_active_account,
)

router = APIRouter()


@router.post("/whatsapp/accounts", response_model=WhatsAppAccountRead)
def create_whatsapp_account(
    payload: WhatsAppAccountCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> WhatsAppAccountRead:
    """Create or upsert WhatsApp account config for current business."""
    return create_or_update_account(
        session=session,
        current_user=current_user,
        data=payload,
    )


@router.get("/whatsapp/accounts/active", response_model=Optional[WhatsAppAccountRead])
def get_whatsapp_account_active(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Optional[WhatsAppAccountRead]:
    """Return active account metadata for current business (no token in response)."""
    return get_active_account(session=session, current_user=current_user)
