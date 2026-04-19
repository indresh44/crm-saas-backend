from typing import List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.models.invoice_template import InvoiceTemplate, InvoiceTemplateListItem
from app.models.invoice_template_item import InvoiceTemplateItem


def create_template(session: Session, template: InvoiceTemplate) -> InvoiceTemplate:
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def update_template(session: Session, template: InvoiceTemplate) -> InvoiceTemplate:
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def delete_template(session: Session, template: InvoiceTemplate) -> None:
    session.delete(template)
    session.commit()


def get_template_by_id(
    session: Session,
    business_id: UUID,
    template_id: UUID,
) -> Optional[InvoiceTemplate]:
    statement = select(InvoiceTemplate).where(
        InvoiceTemplate.id == template_id,
        InvoiceTemplate.business_id == business_id,
    )
    return session.exec(statement).first()


def get_template_with_items(
    session: Session,
    business_id: UUID,
    template_id: UUID,
) -> Optional[InvoiceTemplate]:
    statement = (
        select(InvoiceTemplate)
        .options(selectinload(InvoiceTemplate.items))
        .where(
            InvoiceTemplate.id == template_id,
            InvoiceTemplate.business_id == business_id,
        )
    )
    return session.exec(statement).first()


def list_templates_enriched(
    session: Session,
    business_id: UUID,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[List[InvoiceTemplateListItem], int]:
    items_count_sq = (
        select(
            InvoiceTemplateItem.template_id.label("template_id"),
            func.count(InvoiceTemplateItem.id).label("items_count"),
        )
        .group_by(InvoiceTemplateItem.template_id)
        .subquery()
    )

    statement = (
        select(
            InvoiceTemplate,
            func.coalesce(items_count_sq.c.items_count, 0).label("items_count"),
        )
        .outerjoin(items_count_sq, InvoiceTemplate.id == items_count_sq.c.template_id)
        .where(InvoiceTemplate.business_id == business_id)
    )

    if search:
        statement = statement.where(InvoiceTemplate.name.ilike(f"%{search}%"))

    total_sq = statement.order_by(None).subquery()
    total = session.exec(select(func.count()).select_from(total_sq)).one()

    rows = session.exec(
        statement.order_by(InvoiceTemplate.updated_at.desc()).offset(offset).limit(limit)
    ).all()

    results: list[InvoiceTemplateListItem] = []
    for template, items_count in rows:
        list_item = InvoiceTemplateListItem.model_validate(template, from_attributes=True)
        list_item.items_count = int(items_count)
        results.append(list_item)

    return results, total


def list_items_for_template(
    session: Session,
    template_id: UUID,
) -> List[InvoiceTemplateItem]:
    statement = (
        select(InvoiceTemplateItem)
        .where(InvoiceTemplateItem.template_id == template_id)
        .order_by(InvoiceTemplateItem.sort_order, InvoiceTemplateItem.created_at)
    )
    return list(session.exec(statement).all())


def replace_template_items(
    session: Session,
    template_id: UUID,
    items: List[InvoiceTemplateItem],
) -> List[InvoiceTemplateItem]:
    existing_items = list_items_for_template(session, template_id)
    for item in existing_items:
        session.delete(item)
    session.flush()

    for item in items:
        session.add(item)

    session.commit()
    return list_items_for_template(session, template_id)
