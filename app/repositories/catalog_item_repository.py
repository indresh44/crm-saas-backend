from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.catalog_item import CatalogItem


def create_catalog_item(session: Session, catalog_item: CatalogItem) -> CatalogItem:
    session.add(catalog_item)
    session.commit()
    session.refresh(catalog_item)
    return catalog_item


def get_catalog_item_by_id(session: Session, business_id: UUID, item_id: UUID) -> CatalogItem | None:
    statement = select(CatalogItem).where(
        CatalogItem.id == item_id,
        CatalogItem.business_id == business_id,
    )
    return session.exec(statement).first()


def list_catalog_items(
    session: Session,
    business_id: UUID,
    search: str | None,
    is_active: bool | None = True,
) -> list[CatalogItem]:
    statement = select(CatalogItem).where(CatalogItem.business_id == business_id)

    if search:
        statement = statement.where(CatalogItem.name.ilike(f"%{search}%"))

    if is_active is not None:
        statement = statement.where(CatalogItem.is_active == is_active)

    statement = statement.order_by(CatalogItem.name.asc())
    return list(session.exec(statement).all())


def update_catalog_item(session: Session, catalog_item: CatalogItem) -> CatalogItem:
    session.add(catalog_item)
    session.commit()
    session.refresh(catalog_item)
    return catalog_item


def check_duplicate_name(
    session: Session,
    business_id: UUID,
    name: str,
    exclude_id: UUID | None = None,
) -> bool:
    statement = select(CatalogItem).where(
        CatalogItem.business_id == business_id,
        func.lower(CatalogItem.name) == name.strip().lower(),
    )

    if exclude_id is not None:
        statement = statement.where(CatalogItem.id != exclude_id)

    return session.exec(statement).first() is not None
