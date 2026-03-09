from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.message import Message, MessageCreate
from app.models.user import User
from app.repositories.customer_repository import get_customer_by_id
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.message_repository import (
    create_message as repo_create_message,
    list_messages_for_business,
)


def create_message(
    session: Session,
    current_user: User,
    data: MessageCreate,
) -> Message:
    if data.customer_id is not None:
        customer = get_customer_by_id(
            session=session,
            business_id=current_user.business_id,
            customer_id=data.customer_id,
        )
        if customer is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Customer not found for current business",
            )

    if data.lead_id is not None:
        lead = get_lead_by_id(
            session=session,
            business_id=current_user.business_id,
            lead_id=data.lead_id,
        )
        if lead is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Lead not found for current business",
            )

    message_data = data.model_dump()
    message_data["business_id"] = current_user.business_id
    message = Message(**message_data)
    return repo_create_message(session, message)


def list_messages(
    session: Session,
    current_user: User,
) -> List[Message]:
    return list_messages_for_business(session, business_id=current_user.business_id)

