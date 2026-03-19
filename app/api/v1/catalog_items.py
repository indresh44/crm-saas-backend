from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.catalog_item import CatalogItemCreate, CatalogItemRead, CatalogItemUpdate
from app.models.user import User
from app.services.catalog_item_service import (
    create_catalog_item as service_create_catalog_item,
    deactivate_catalog_item as service_deactivate_catalog_item,
    get_catalog_item as service_get_catalog_item,
    list_catalog_items as service_list_catalog_items,
    update_catalog_item as service_update_catalog_item,
)

router = APIRouter()


@router.post("/catalog-items", response_model=CatalogItemRead, status_code=status.HTTP_201_CREATED)
def create_catalog_item(
    payload: CatalogItemCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CatalogItemRead:
    catalog_item = service_create_catalog_item(session=session, current_user=current_user, data=payload)
    return catalog_item


@router.get("/catalog-items", response_model=List[CatalogItemRead])
def list_catalog_items(
    search: str | None = None,
    is_active: bool | None = True,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[CatalogItemRead]:
    catalog_items = service_list_catalog_items(
        session=session,
        current_user=current_user,
        search=search,
        is_active=is_active,
    )
    return catalog_items


@router.get("/catalog-items/{item_id}", response_model=CatalogItemRead)
def get_catalog_item(
    item_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CatalogItemRead:
    catalog_item = service_get_catalog_item(session=session, current_user=current_user, item_id=item_id)
    return catalog_item


@router.patch("/catalog-items/{item_id}", response_model=CatalogItemRead)
def update_catalog_item(
    item_id: UUID,
    payload: CatalogItemUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CatalogItemRead:
    catalog_item = service_update_catalog_item(
        session=session,
        current_user=current_user,
        item_id=item_id,
        data=payload,
    )
    return catalog_item


@router.delete("/catalog-items/{item_id}", response_model=CatalogItemRead)
def deactivate_catalog_item(
    item_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CatalogItemRead:
    catalog_item = service_deactivate_catalog_item(
        session=session,
        current_user=current_user,
        item_id=item_id,
    )
    return catalog_item
