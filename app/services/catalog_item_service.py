from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.catalog_item import CatalogItem, CatalogItemCreate, CatalogItemUpdate
from app.models.enums import CatalogItemUnit
from app.models.user import User
from app.repositories.catalog_item_repository import (
    check_duplicate_name,
    create_catalog_item as repo_create_catalog_item,
    get_catalog_item_by_id,
    list_catalog_items as repo_list_catalog_items,
    update_catalog_item as repo_update_catalog_item,
)


def create_catalog_item(
    session: Session,
    current_user: User,
    data: CatalogItemCreate,
) -> CatalogItem:
    if check_duplicate_name(session, current_user.business_id, data.name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A catalog item with this name already exists",
        )

    if data.unit == CatalogItemUnit.CUSTOM and not data.custom_unit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="custom_unit is required when unit is custom",
        )

    catalog_item_data = data.model_dump()
    catalog_item_data["business_id"] = current_user.business_id
    catalog_item = CatalogItem(**catalog_item_data)
    return repo_create_catalog_item(session, catalog_item)


def list_catalog_items(
    session: Session,
    current_user: User,
    search: str | None,
    is_active: bool | None,
) -> list[CatalogItem]:
    return repo_list_catalog_items(
        session=session,
        business_id=current_user.business_id,
        search=search,
        is_active=is_active,
    )


def get_catalog_item(
    session: Session,
    current_user: User,
    item_id: UUID,
) -> CatalogItem:
    catalog_item = get_catalog_item_by_id(session, current_user.business_id, item_id)
    if catalog_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog item not found",
        )
    return catalog_item


def update_catalog_item(
    session: Session,
    current_user: User,
    item_id: UUID,
    data: CatalogItemUpdate,
) -> CatalogItem:
    catalog_item = get_catalog_item(session, current_user, item_id)

    update_data = data.model_dump(exclude_unset=True)

    new_name = update_data.get("name")
    if new_name is not None and check_duplicate_name(
        session,
        current_user.business_id,
        new_name,
        exclude_id=item_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A catalog item with this name already exists",
        )

    next_unit = update_data.get("unit", catalog_item.unit)
    next_custom_unit = update_data.get("custom_unit", catalog_item.custom_unit)
    if next_unit == CatalogItemUnit.CUSTOM and not next_custom_unit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="custom_unit is required when unit is custom",
        )

    for field, value in update_data.items():
        setattr(catalog_item, field, value)

    return repo_update_catalog_item(session, catalog_item)


def deactivate_catalog_item(
    session: Session,
    current_user: User,
    item_id: UUID,
) -> CatalogItem:
    catalog_item = get_catalog_item(session, current_user, item_id)
    catalog_item.is_active = False
    return repo_update_catalog_item(session, catalog_item)
