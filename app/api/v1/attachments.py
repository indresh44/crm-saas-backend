from typing import List

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.attachment import AttachmentCreate, AttachmentRead
from app.models.user import User
from app.services.attachment_service import (
    create_attachment as service_create_attachment,
    list_attachments as service_list_attachments,
)

router = APIRouter()


@router.post("/attachments", response_model=AttachmentRead)
def create_attachment(
    payload: AttachmentCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AttachmentRead:
    attachment = service_create_attachment(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return attachment


@router.get("/attachments", response_model=List[AttachmentRead])
def list_attachments(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[AttachmentRead]:
    attachments = service_list_attachments(session=session, current_user=current_user)
    return attachments

