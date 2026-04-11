import uuid
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.models.attachment import Attachment
from app.models.enums import AttachmentEntityType
from app.models.invoice_item import InvoiceItem
from app.models.payment import Payment
from app.models.user import User
from app.repositories.attachment_repository import (
    delete_attachment as repository_delete_attachment,
    get_attachment_by_id,
    list_attachments_for_business,
    list_attachments_for_entity,
    list_attachments_for_entities,
)
from app.repositories.catalog_item_repository import get_catalog_item_by_id
from app.repositories.invoice_repository import get_invoice_by_id
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.quote_repository import get_quote_by_id
from app.repositories.task_repository import get_task_by_id
from app.core.storage import delete_file_by_public_url, upload_file


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
    elif entity_type == AttachmentEntityType.CATALOG:
        entity = get_catalog_item_by_id(
            session=session,
            business_id=business_id,
            item_id=entity_id,
        )
    elif entity_type == AttachmentEntityType.INVOICE_ITEM:
        entity = session.exec(
            select(InvoiceItem).where(InvoiceItem.id == entity_id)
        ).first()

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

    # Auto-set sort_order and is_primary for orderable entity types
    att_sort_order = 0
    att_is_primary = False
    if entity_type in (AttachmentEntityType.INVOICE_ITEM, AttachmentEntityType.CATALOG):
        existing_max = session.exec(
            select(func.coalesce(func.max(Attachment.sort_order), -1)).where(
                Attachment.entity_type == entity_type,
                Attachment.entity_id == entity_id,
                Attachment.business_id == business_id,
            )
        ).one()
        att_sort_order = existing_max + 1

        existing_count = session.exec(
            select(func.count(Attachment.id)).where(
                Attachment.entity_type == entity_type,
                Attachment.entity_id == entity_id,
                Attachment.business_id == business_id,
            )
        ).one()
        if existing_count == 0:
            att_is_primary = True

    attachment = Attachment(
        business_id=business_id,
        entity_type=entity_type,
        entity_id=entity_id,
        filename=filename,
        file_url=file_url,
        file_size=file_size,
        sort_order=att_sort_order,
        is_primary=att_is_primary,
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


def list_attachments_batch(
    session: Session,
    current_user: User,
    entity_type: AttachmentEntityType,
    entity_ids: list[UUID],
) -> dict[str, List[Attachment]]:
    attachments = list_attachments_for_entities(
        session=session,
        business_id=current_user.business_id,
        entity_type=entity_type,
        entity_ids=entity_ids,
    )

    grouped: dict[str, List[Attachment]] = {str(entity_id): [] for entity_id in entity_ids}
    for attachment in attachments:
        key = str(attachment.entity_id)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(attachment)
    return grouped


def delete_attachment(
    session: Session,
    current_user: User,
    attachment_id: UUID,
) -> None:
    attachment = get_attachment_by_id(
        session=session,
        business_id=current_user.business_id,
        attachment_id=attachment_id,
    )
    if attachment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found",
        )

    try:
        delete_file_by_public_url(attachment.file_url)
    except Exception:
        # Keep DB state consistent even if remote object cleanup fails.
        pass
    repository_delete_attachment(session, attachment)
