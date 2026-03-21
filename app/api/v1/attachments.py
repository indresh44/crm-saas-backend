from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.attachment import AttachmentRead
from app.models.enums import AttachmentEntityType
from app.models.user import User
from app.services.attachment_service import (
    delete_attachment as service_delete_attachment,
    list_attachments_batch as service_list_attachments_batch,
    list_attachments as service_list_attachments,
    upload_and_save_attachment as service_upload_and_save_attachment,
)

router = APIRouter()


@router.get("/attachments", response_model=List[AttachmentRead])
def list_attachments(
    entity_type: str,
    entity_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[AttachmentRead]:
    try:
        entity_type_enum = AttachmentEntityType(entity_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid entity_type",
        ) from exc

    attachments = service_list_attachments(
        session=session,
        current_user=current_user,
        entity_type=entity_type_enum,
        entity_id=entity_id,
    )
    return attachments


@router.get("/attachments/batch", response_model=dict[str, List[AttachmentRead]])
def list_attachments_batch(
    entity_type: str,
    entity_ids: List[UUID] = Query(default=[]),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, List[AttachmentRead]]:
    try:
        entity_type_enum = AttachmentEntityType(entity_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid entity_type",
        ) from exc

    return service_list_attachments_batch(
        session=session,
        current_user=current_user,
        entity_type=entity_type_enum,
        entity_ids=entity_ids,
    )


@router.post("/attachments/upload", response_model=AttachmentRead)
async def upload_attachment(
    file: UploadFile = File(...),
    entity_type: str = Form(...),
    entity_id: UUID = Form(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AttachmentRead:
    allowed_content_types = {
        "image/jpeg",
        "image/png",
        "application/pdf",
    }
    max_file_size = 10 * 1024 * 1024

    try:
        entity_type_enum = AttachmentEntityType(entity_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid entity_type",
        ) from exc

    if file.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid content type",
        )

    contents = await file.read()
    if len(contents) > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 10MB",
        )

    attachment = service_upload_and_save_attachment(
        session=session,
        business_id=current_user.business_id,
        entity_type=entity_type_enum,
        entity_id=entity_id,
        file_bytes=contents,
        filename=file.filename or "attachment",
        content_type=file.content_type,
        file_size=len(contents),
    )
    return attachment


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    service_delete_attachment(
        session=session,
        current_user=current_user,
        attachment_id=attachment_id,
    )
