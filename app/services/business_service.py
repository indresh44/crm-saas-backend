from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.business import Business, BusinessCreate
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
