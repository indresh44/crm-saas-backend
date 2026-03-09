from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.attachment import Attachment, AttachmentCreate
from app.models.user import User
from app.repositories.attachment_repository import (
    create_attachment as repo_create_attachment,
    list_attachments_for_business,
)
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.quote_repository import get_quote_by_id
from app.repositories.invoice_repository import get_invoice_by_id
from app.repositories.task_repository import get_task_by_id


def create_attachment(
    session: Session,
    current_user: User,
    data: AttachmentCreate,
) -> Attachment:
    # Basic validation that the entity exists in the same business
    if data.entity_type.value == "lead":
        entity = get_lead_by_id(
            session=session,
            business_id=current_user.business_id,
            lead_id=data.entity_id,
        )
    elif data.entity_type.value == "quote":
        entity = get_quote_by_id(
            session=session,
            business_id=current_user.business_id,
            quote_id=data.entity_id,
        )
    elif data.entity_type.value == "invoice":
        entity = get_invoice_by_id(
            session=session,
            business_id=current_user.business_id,
            invoice_id=data.entity_id,
        )
    elif data.entity_type.value == "task":
        entity = get_task_by_id(
            session=session,
            business_id=current_user.business_id,
            task_id=data.entity_id,
        )
    else:
        entity = None

    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attachment entity not found for current business",
        )

    attachment_data = data.model_dump()
    attachment_data["business_id"] = current_user.business_id
    attachment = Attachment(**attachment_data)
    return repo_create_attachment(session, attachment)


def list_attachments(
    session: Session,
    current_user: User,
) -> List[Attachment]:
    return list_attachments_for_business(session, business_id=current_user.business_id)

