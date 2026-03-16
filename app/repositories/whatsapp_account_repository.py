from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.whatsapp_account import WhatsAppAccount


def create_whatsapp_account(session: Session, account: WhatsAppAccount) -> WhatsAppAccount:
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def get_whatsapp_account_by_id(
    session: Session,
    business_id: UUID,
    account_id: UUID,
) -> Optional[WhatsAppAccount]:
    statement = select(WhatsAppAccount).where(
        WhatsAppAccount.id == account_id,
        WhatsAppAccount.business_id == business_id,
    )
    return session.exec(statement).first()


def get_active_whatsapp_account_for_business(
    session: Session,
    business_id: UUID,
) -> Optional[WhatsAppAccount]:
    statement = select(WhatsAppAccount).where(
        WhatsAppAccount.business_id == business_id,
        WhatsAppAccount.is_active == True,
    )
    return session.exec(statement).first()


def get_whatsapp_account_by_phone_number_id(
    session: Session,
    phone_number_id: str,
) -> Optional[WhatsAppAccount]:
    statement = select(WhatsAppAccount).where(
        WhatsAppAccount.phone_number_id == phone_number_id,
    )
    return session.exec(statement).first()


def update_whatsapp_account(session: Session, account: WhatsAppAccount) -> WhatsAppAccount:
    session.add(account)
    session.commit()
    session.refresh(account)
    return account
