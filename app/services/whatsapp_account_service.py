from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.user import User
from app.models.whatsapp_account import WhatsAppAccount, WhatsAppAccountCreate, WhatsAppAccountRead
from app.repositories.whatsapp_account_repository import (
    create_whatsapp_account,
    get_active_whatsapp_account_for_business,
    get_whatsapp_account_by_phone_number_id,
    update_whatsapp_account,
)


def create_or_update_account(
    session: Session,
    current_user: User,
    data: WhatsAppAccountCreate,
) -> WhatsAppAccountRead:
    """Create or upsert WhatsApp account config for current business. One active per business."""
    business_id = current_user.business_id
    existing = get_active_whatsapp_account_for_business(session, business_id)
    if existing:
        if existing.phone_number_id != data.phone_number_id:
            # Different phone number: deactivate current and create new, or reject.
            # For simplicity: update existing with new data (treat as re-link same business).
            existing.meta_app_id = data.meta_app_id
            existing.waba_id = data.waba_id
            existing.phone_number_id = data.phone_number_id
            existing.display_phone_number = data.display_phone_number
            existing.verified_name = data.verified_name
            existing.access_token = data.access_token
            existing.token_type = data.token_type
            existing.webhook_verify_token = data.webhook_verify_token
            existing.app_secret = data.app_secret
            existing.is_active = data.is_active
            update_whatsapp_account(session, existing)
            return _to_read(existing)
        for key, value in data.model_dump().items():
            setattr(existing, key, value)
        update_whatsapp_account(session, existing)
        return _to_read(existing)
    account = WhatsAppAccount(business_id=business_id, **data.model_dump())
    create_whatsapp_account(session, account)
    return _to_read(account)


def get_active_account(
    session: Session,
    current_user: User,
) -> WhatsAppAccountRead | None:
    """Return active account metadata for current business (no token in response)."""
    account = get_active_whatsapp_account_for_business(session, current_user.business_id)
    if account is None:
        return None
    return _to_read(account)


def _to_read(account: WhatsAppAccount) -> WhatsAppAccountRead:
    return WhatsAppAccountRead(
        id=account.id,
        business_id=account.business_id,
        meta_app_id=account.meta_app_id,
        waba_id=account.waba_id,
        phone_number_id=account.phone_number_id,
        display_phone_number=account.display_phone_number,
        verified_name=account.verified_name,
        token_type=account.token_type,
        is_active=account.is_active,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )
