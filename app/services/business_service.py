from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.business import Business, BusinessCreate, BusinessSettingsUpdate
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.business_repository import (
    create_business as repo_create_business,
    get_business_by_id,
    list_businesses as repo_list_businesses,
)
from app.services.pipeline_service import create_default_pipeline


def create_business(session: Session, data: BusinessCreate) -> Business:
    business = Business(**data.model_dump())
    business = repo_create_business(session, business)
    create_default_pipeline(session, business.id)
    return business


def get_business(session: Session, business_id: UUID) -> Business:
    business = get_business_by_id(session, business_id)
    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found",
        )
    return business


def list_businesses(session: Session) -> list[Business]:
    return repo_list_businesses(session)


def get_business_settings(
    session: Session,
    current_user: User,
) -> Business:
    """Get the current user's business with all settings."""
    business = get_business_by_id(session, current_user.business_id)
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found",
        )
    return business


def update_business_settings(
    session: Session,
    current_user: User,
    data: BusinessSettingsUpdate,
) -> Business:
    """
    Update business profile and invoice settings.
    Only the business owner or manager can update settings.
    """
    if current_user.role not in (UserRole.OWNER, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the business owner or manager can update settings",
        )

    business = get_business_settings(session, current_user)
    update_data = data.model_dump(exclude_unset=True)

    if "default_due_days" in update_data and update_data["default_due_days"] is not None:
        if update_data["default_due_days"] < 0 or update_data["default_due_days"] > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Default due days must be between 0 and 365",
            )

    for key, value in update_data.items():
        setattr(business, key, value)

    session.add(business)
    session.commit()
    session.refresh(business)
    return business


def update_business_logo(
    session: Session,
    current_user: User,
    file_url: str,
) -> Business:
    """Update business logo URL after file is uploaded to R2."""
    if current_user.role not in (UserRole.OWNER, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the business owner or manager can update settings",
        )

    business = get_business_settings(session, current_user)
    business.logo_url = file_url
    session.add(business)
    session.commit()
    session.refresh(business)
    return business
