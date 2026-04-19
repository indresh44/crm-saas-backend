from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.catalog_item import CatalogItem
from app.models.invoice_template import (
    InvoiceTemplate,
    InvoiceTemplateCreate,
    InvoiceTemplateFromInvoice,
    InvoiceTemplateListItem,
    InvoiceTemplateUpdate,
)
from app.models.invoice_template_item import (
    InvoiceTemplateItem,
    InvoiceTemplateItemCreate,
)
from app.models.user import User
from app.repositories.invoice_repository import get_invoice_with_items
from app.repositories.invoice_template_repository import (
    create_template as repo_create_template,
    delete_template as repo_delete_template,
    get_template_by_id,
    get_template_with_items,
    list_items_for_template,
    list_templates_enriched,
    replace_template_items,
    update_template as repo_update_template,
)
from app.services.line_item_calculator import calculate_totals


PricingMode = Literal["template", "catalog"]


def _build_template_items(
    template_id: UUID,
    items_with_totals: list[dict],
) -> List[InvoiceTemplateItem]:
    return [
        InvoiceTemplateItem(
            template_id=template_id,
            catalog_item_id=item.get("catalog_item_id"),
            name=item.get("name", ""),
            description=item["description"],
            unit=item.get("unit", "piece"),
            quantity=item["quantity"],
            unit_price=item["unit_price"],
            gst_percent=item["gst_percent"],
            amount=item["amount"],
            sac_code=item.get("sac_code"),
            deliverables=item.get("deliverables"),
            sort_order=item.get("sort_order", idx),
        )
        for idx, item in enumerate(items_with_totals)
    ]


def _validate_items(items: list[InvoiceTemplateItemCreate]) -> None:
    for item in items:
        item.validate_gst()


def list_templates(
    session: Session,
    current_user: User,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[List[InvoiceTemplateListItem], int]:
    return list_templates_enriched(
        session=session,
        business_id=current_user.business_id,
        search=search,
        limit=limit,
        offset=offset,
    )


def get_template(
    session: Session,
    current_user: User,
    template_id: UUID,
) -> InvoiceTemplate:
    template = get_template_with_items(
        session=session,
        business_id=current_user.business_id,
        template_id=template_id,
    )
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )
    return template


def create_template(
    session: Session,
    current_user: User,
    data: InvoiceTemplateCreate,
    source_invoice_id: Optional[UUID] = None,
) -> InvoiceTemplate:
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Template name is required",
        )

    _validate_items(data.items)
    totals = calculate_totals(data.items)

    template = InvoiceTemplate(
        business_id=current_user.business_id,
        name=name,
        source_invoice_id=source_invoice_id,
        subtotal=totals["subtotal"],
        tax_total=totals["tax_total"],
        total_amount=totals["total_amount"],
    )
    session.add(template)
    session.flush()

    items = _build_template_items(
        template_id=template.id,
        items_with_totals=totals["items_with_totals"],
    )
    if items:
        session.add_all(items)

    session.commit()
    session.refresh(template)
    return template


def create_template_from_invoice(
    session: Session,
    current_user: User,
    invoice_id: UUID,
    data: InvoiceTemplateFromInvoice,
) -> InvoiceTemplate:
    invoice = get_invoice_with_items(
        session=session,
        business_id=current_user.business_id,
        invoice_id=invoice_id,
    )
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    item_payloads = [
        InvoiceTemplateItemCreate(
            name=item.name,
            description=item.description,
            unit=item.unit,
            catalog_item_id=item.catalog_item_id,
            quantity=item.quantity,
            unit_price=item.unit_price,
            gst_percent=item.gst_percent,
            sac_code=item.sac_code,
            deliverables=list(item.deliverables) if item.deliverables else None,
            sort_order=idx,
        )
        for idx, item in enumerate(invoice.items)
    ]

    return create_template(
        session=session,
        current_user=current_user,
        data=InvoiceTemplateCreate(name=data.name, items=item_payloads),
        source_invoice_id=invoice.id,
    )


def update_template(
    session: Session,
    current_user: User,
    template_id: UUID,
    data: InvoiceTemplateUpdate,
) -> InvoiceTemplate:
    template = get_template_with_items(
        session=session,
        business_id=current_user.business_id,
        template_id=template_id,
    )
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )

    if data.name is not None:
        new_name = data.name.strip()
        if not new_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Template name is required",
            )
        template.name = new_name

    if data.items is not None:
        _validate_items(data.items)
        totals = calculate_totals(data.items)
        template.subtotal = totals["subtotal"]
        template.tax_total = totals["tax_total"]
        template.total_amount = totals["total_amount"]

        items = _build_template_items(
            template_id=template.id,
            items_with_totals=totals["items_with_totals"],
        )
        replace_template_items(session, template_id=template.id, items=items)

    template = repo_update_template(session, template)
    return template


def delete_template(
    session: Session,
    current_user: User,
    template_id: UUID,
) -> None:
    template = get_template_by_id(
        session=session,
        business_id=current_user.business_id,
        template_id=template_id,
    )
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )
    repo_delete_template(session, template)


def duplicate_template(
    session: Session,
    current_user: User,
    template_id: UUID,
) -> InvoiceTemplate:
    source = get_template_with_items(
        session=session,
        business_id=current_user.business_id,
        template_id=template_id,
    )
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )

    item_payloads = [
        InvoiceTemplateItemCreate(
            name=item.name,
            description=item.description,
            unit=item.unit,
            catalog_item_id=item.catalog_item_id,
            quantity=item.quantity,
            unit_price=item.unit_price,
            gst_percent=item.gst_percent,
            sac_code=item.sac_code,
            deliverables=list(item.deliverables) if item.deliverables else None,
            sort_order=item.sort_order,
        )
        for item in source.items
    ]

    return create_template(
        session=session,
        current_user=current_user,
        data=InvoiceTemplateCreate(name=f"{source.name} (copy)", items=item_payloads),
        source_invoice_id=None,
    )


def get_priced_items(
    session: Session,
    current_user: User,
    template_id: UUID,
    mode: PricingMode,
) -> dict:
    template = get_template(
        session=session,
        current_user=current_user,
        template_id=template_id,
    )

    saved_items = list_items_for_template(session, template.id)

    catalog_map: dict[UUID, CatalogItem] = {}
    if mode == "catalog":
        catalog_ids = {item.catalog_item_id for item in saved_items if item.catalog_item_id}
        if catalog_ids:
            rows = session.exec(
                select(CatalogItem).where(
                    CatalogItem.id.in_(catalog_ids),
                    CatalogItem.business_id == current_user.business_id,
                )
            ).all()
            catalog_map = {row.id: row for row in rows}

    priced_items: list[dict] = []
    for item in saved_items:
        unit_price = item.unit_price
        gst_percent = item.gst_percent

        if mode == "catalog" and item.catalog_item_id and item.catalog_item_id in catalog_map:
            catalog = catalog_map[item.catalog_item_id]
            unit_price = catalog.default_rate
            gst_percent = catalog.gst_percent

        price_changed = (
            unit_price != item.unit_price or gst_percent != item.gst_percent
        )

        priced_items.append({
            "id": str(item.id),
            "catalog_item_id": str(item.catalog_item_id) if item.catalog_item_id else None,
            "name": item.name,
            "description": item.description,
            "unit": item.unit,
            "quantity": float(item.quantity),
            "unit_price": float(unit_price),
            "gst_percent": float(gst_percent),
            "sac_code": item.sac_code,
            "deliverables": item.deliverables,
            "sort_order": item.sort_order,
            "template_unit_price": float(item.unit_price),
            "template_gst_percent": float(item.gst_percent),
            "price_changed": price_changed,
            "has_catalog_link": item.catalog_item_id is not None,
        })

    totals_input = [
        type("Item", (), {
            "quantity": Decimal(str(it["quantity"])),
            "unit_price": Decimal(str(it["unit_price"])),
            "gst_percent": Decimal(str(it["gst_percent"])),
        })()
        for it in priced_items
    ]
    totals = calculate_totals(totals_input)

    return {
        "template_id": str(template.id),
        "template_name": template.name,
        "template_saved_at": template.updated_at.isoformat(),
        "mode": mode,
        "items": priced_items,
        "subtotal": float(totals["subtotal"]),
        "tax_total": float(totals["tax_total"]),
        "total_amount": float(totals["total_amount"]),
    }
