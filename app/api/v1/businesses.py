from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlmodel import Session

from app.core.dependencies import get_current_user
from app.core.database import get_session
from app.core.storage import upload_file
from app.models.business import (
    BusinessCreate,
    BusinessRead,
    BusinessSettingsRead,
    BusinessSettingsUpdate,
)
from app.models.user import User
from app.services.business_service import (
    create_business as service_create_business,
    get_business as service_get_business,
    get_business_settings as service_get_business_settings,
    list_businesses as service_list_businesses,
    update_business_logo as service_update_business_logo,
    update_business_settings as service_update_business_settings,
)

router = APIRouter()


@router.post("/businesses", response_model=BusinessRead)
def create_business(
    payload: BusinessCreate,
    session: Session = Depends(get_session),
) -> BusinessRead:
    business = service_create_business(session=session, data=payload)
    return business


@router.get("/businesses", response_model=List[BusinessRead])
def list_businesses(
    session: Session = Depends(get_session),
) -> List[BusinessRead]:
    businesses = service_list_businesses(session=session)
    return businesses


@router.get("/businesses/settings", response_model=BusinessSettingsRead)
def get_settings(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BusinessSettingsRead:
    return service_get_business_settings(session, current_user)


@router.patch("/businesses/settings", response_model=BusinessSettingsRead)
def update_settings(
    payload: BusinessSettingsUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BusinessSettingsRead:
    return service_update_business_settings(session, current_user, payload)


@router.post("/businesses/logo", response_model=BusinessSettingsRead)
async def upload_logo(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BusinessSettingsRead:
    allowed_types = {"image/jpeg", "image/png", "image/jpg"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logo must be JPG or PNG",
        )

    contents = await file.read()
    if len(contents) > 2 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logo must be under 2MB",
        )

    file_url = upload_file(
        file_bytes=contents,
        filename=file.filename or "logo",
        content_type=file.content_type,
        folder=f"logos/{current_user.business_id}",
    )

    return service_update_business_logo(session, current_user, file_url)


@router.get("/businesses/{id}", response_model=BusinessRead)
def get_business(
    id: UUID,
    session: Session = Depends(get_session),
) -> BusinessRead:
    business = service_get_business(session=session, business_id=id)
    return business
