from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.invoice_template import (
    InvoiceTemplateCreate,
    InvoiceTemplateFromInvoice,
    InvoiceTemplateListResponse,
    InvoiceTemplateRead,
    InvoiceTemplateReadWithItems,
    InvoiceTemplateUpdate,
)
from app.models.user import User
from app.services.invoice_template_service import (
    create_template as service_create_template,
    create_template_from_invoice as service_create_from_invoice,
    delete_template as service_delete_template,
    duplicate_template as service_duplicate_template,
    get_priced_items as service_get_priced_items,
    get_template as service_get_template,
    list_templates as service_list_templates,
    update_template as service_update_template,
)

router = APIRouter()


@router.get("/invoice-templates", response_model=InvoiceTemplateListResponse)
def list_invoice_templates(
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceTemplateListResponse:
    items, total = service_list_templates(
        session=session,
        current_user=current_user,
        search=search,
        limit=limit,
        offset=offset,
    )
    return InvoiceTemplateListResponse(items=items, total=total)


@router.post("/invoice-templates", response_model=InvoiceTemplateReadWithItems)
def create_invoice_template(
    payload: InvoiceTemplateCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceTemplateReadWithItems:
    template = service_create_template(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return service_get_template(
        session=session,
        current_user=current_user,
        template_id=template.id,
    )


@router.post(
    "/invoice-templates/from-invoice/{invoice_id}",
    response_model=InvoiceTemplateReadWithItems,
)
def create_template_from_invoice(
    invoice_id: UUID,
    payload: InvoiceTemplateFromInvoice,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceTemplateReadWithItems:
    template = service_create_from_invoice(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
        data=payload,
    )
    return service_get_template(
        session=session,
        current_user=current_user,
        template_id=template.id,
    )


@router.get(
    "/invoice-templates/{template_id}",
    response_model=InvoiceTemplateReadWithItems,
)
def get_invoice_template(
    template_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceTemplateReadWithItems:
    return service_get_template(
        session=session,
        current_user=current_user,
        template_id=template_id,
    )


@router.patch(
    "/invoice-templates/{template_id}",
    response_model=InvoiceTemplateReadWithItems,
)
def update_invoice_template(
    template_id: UUID,
    payload: InvoiceTemplateUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceTemplateReadWithItems:
    service_update_template(
        session=session,
        current_user=current_user,
        template_id=template_id,
        data=payload,
    )
    return service_get_template(
        session=session,
        current_user=current_user,
        template_id=template_id,
    )


@router.delete("/invoice-templates/{template_id}", status_code=204)
def delete_invoice_template(
    template_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    service_delete_template(
        session=session,
        current_user=current_user,
        template_id=template_id,
    )


@router.post(
    "/invoice-templates/{template_id}/duplicate",
    response_model=InvoiceTemplateReadWithItems,
)
def duplicate_invoice_template(
    template_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceTemplateReadWithItems:
    template = service_duplicate_template(
        session=session,
        current_user=current_user,
        template_id=template_id,
    )
    return service_get_template(
        session=session,
        current_user=current_user,
        template_id=template.id,
    )


@router.get("/invoice-templates/{template_id}/priced")
def get_template_priced_items(
    template_id: UUID,
    mode: Literal["template", "catalog"] = Query(default="template"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    return service_get_priced_items(
        session=session,
        current_user=current_user,
        template_id=template_id,
        mode=mode,
    )
