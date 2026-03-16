from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.models.business import BusinessCreate, BusinessRead
from app.services.business_service import (
    create_business as service_create_business,
    get_business as service_get_business,
    list_businesses as service_list_businesses,
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


@router.get("/businesses/{id}", response_model=BusinessRead)
def get_business(
    id: UUID,
    session: Session = Depends(get_session),
) -> BusinessRead:
    business = service_get_business(session=session, business_id=id)
    return business
