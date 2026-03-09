from typing import List

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.message import MessageCreate, MessageRead
from app.models.user import User
from app.services.message_service import (
    create_message as service_create_message,
    list_messages as service_list_messages,
)

router = APIRouter()


@router.post("/messages", response_model=MessageRead)
def create_message(
    payload: MessageCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MessageRead:
    message = service_create_message(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return message


@router.get("/messages", response_model=List[MessageRead])
def list_messages(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[MessageRead]:
    messages = service_list_messages(session=session, current_user=current_user)
    return messages

