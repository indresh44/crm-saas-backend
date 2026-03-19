import uuid
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.attachment import Attachment
from app.models.enums import AttachmentEntityType
from app.models.payment import Payment
from app.models.user import User
from app.repositories.attachment_repository import list_attachments_for_entity, list_attachments_for_business
from app.repositories.invoice_repository import get_invoice_by_id
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.quote_repository import get_quote_by_id
from app.repositories.task_repository import get_task_by_id
from app.core.storage import upload_file


def upload_and_save_attachment(
    session: Session,
    business_id: uuid.UUID,
    entity_type: AttachmentEntityType,
    entity_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    file_size: int,
) -> Attachment:
    entity = None
    if entity_type == AttachmentEntityType.LEAD:
        entity = get_lead_by_id(
            session=session,
            business_id=business_id,
            lead_id=entity_id,
        )
    elif entity_type == AttachmentEntityType.QUOTE:
        entity = get_quote_by_id(
            session=session,
            business_id=business_id,
            quote_id=entity_id,
        )
    elif entity_type == AttachmentEntityType.INVOICE:
        entity = get_invoice_by_id(
            session=session,
            business_id=business_id,
            invoice_id=entity_id,
        )
    elif entity_type == AttachmentEntityType.TASK:
        entity = get_task_by_id(
            session=session,
            business_id=business_id,
            task_id=entity_id,
        )
    elif entity_type == AttachmentEntityType.PAYMENT:
        statement = select(Payment).where(
            Payment.business_id == business_id,
            Payment.id == entity_id,
        )
        entity = session.exec(statement).first()

    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attachment entity not found for current business",
        )

    file_url = upload_file(
        file_bytes,
        filename,
        content_type,
        folder=str(entity_type.value),
    )
    attachment = Attachment(
        business_id=business_id,
        entity_type=entity_type,
        entity_id=entity_id,
        filename=filename,
        file_url=file_url,
        file_size=file_size,
    )
    session.add(attachment)
    session.commit()
    session.refresh(attachment)
    return attachment


def list_attachments(
    session: Session,
    current_user: User,
    entity_type: AttachmentEntityType | None = None,
    entity_id: UUID | None = None,
) -> List[Attachment]:
    if entity_type is not None and entity_id is not None:
        return list_attachments_for_entity(
            session=session,
            business_id=current_user.business_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    return list_attachments_for_business(session, business_id=current_user.business_id)
