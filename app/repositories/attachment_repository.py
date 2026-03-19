from typing import List
from uuid import UUID

from sqlmodel import Session, select

from app.models.attachment import Attachment
from app.models.enums import AttachmentEntityType


def list_attachments_for_business(
    session: Session,
    business_id: UUID,
) -> List[Attachment]:
    statement = select(Attachment).where(Attachment.business_id == business_id)
    return list(session.exec(statement).all())


def list_attachments_for_entity(
    session: Session,
    business_id: UUID,
    entity_type: AttachmentEntityType,
    entity_id: UUID,
) -> List[Attachment]:
    statement = (
        select(Attachment)
        .where(Attachment.business_id == business_id)
        .where(Attachment.entity_type == entity_type)
        .where(Attachment.entity_id == entity_id)
    )
    return list(session.exec(statement).all())
