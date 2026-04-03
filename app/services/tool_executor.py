from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.models.enums import InvoiceStatus, UserRole
from app.models.lead import LeadCreate
from app.models.lead_followup import LeadFollowupCreate
from app.models.user import User
from app.services import catalog_item_service, customer_service, dashboard_service, invoice_service
from app.services import lead_followup_service, lead_service, payment_service, pipeline_service


class ToolResult(BaseModel):
    success: bool
    data: dict[str, Any]
    is_write: bool
    action: dict[str, Any] | None = None
    error: str | None = None


class ToolExecutor:
    READ_ONLY_TOOLS = {
        "get_todays_followups",
        "get_overdue_followups",
        "get_lead_details",
        "get_customer_outstanding",
        "list_customer_invoices",
        "search_customer",
        "search_lead",
        "get_dashboard_summary",
        "list_customer_payments",
        "get_lead_followups",
        "get_catalog_items",
        "generate_invoice_pdf",
    }

    WRITE_TOOLS = {
        "create_lead",
        "update_lead_stage",
        "schedule_followup",
        "prepare_invoice",
        "add_lead_note",
    }

    def __init__(self, session: Session, current_user: User) -> None:
        self.session = session
        self.current_user = current_user

    async def execute(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        business_id: UUID,
        context_type: str,
        context_id: UUID | None,
    ) -> ToolResult:
        if business_id != self.current_user.business_id:
            return ToolResult(
                success=False,
                data={},
                is_write=tool_name in self.WRITE_TOOLS,
                error="Business context mismatch",
            )

        normalized_args = self._apply_context_fallbacks(
            tool_name=tool_name,
            tool_args=tool_args,
            context_type=context_type,
            context_id=context_id,
        )

        handler = getattr(self, f"_{tool_name}", None)
        if handler is None:
            return ToolResult(
                success=False,
                data={},
                is_write=tool_name in self.WRITE_TOOLS,
                error=f"Unsupported tool: {tool_name}",
            )

        try:
            payload = await handler(business_id=business_id, args=normalized_args)
            is_write = tool_name in self.WRITE_TOOLS
            
            # For write tools, override data sent to LLM
            # so it knows the action is NOT yet executed
            if is_write:
                data = {
                    "status": "pending_confirmation",
                    "message": (
                        f"Prepared {tool_name} for user review. "
                        "The action has NOT been executed yet. "
                        "Ask the user to review the details and confirm. "
                        "Do NOT say it has been created or completed."
                    ),
                }
            else:
                data = payload.get("data", {})
            return ToolResult(
                success=True,
                data=data,
                is_write=is_write,
                action=payload.get("action"),
            )
        except HTTPException as exc:
            return ToolResult(
                success=False,
                data={},
                is_write=tool_name in self.WRITE_TOOLS,
                error=str(exc.detail),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                data={},
                is_write=tool_name in self.WRITE_TOOLS,
                error=str(exc),
            )

    async def _get_todays_followups(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        followups = lead_followup_service.list_todays_followups(
            session=self.session,
            current_user=self.current_user,
        )
        items = []
        for followup in followups:
            lead = lead_service.get_lead(self.session, self.current_user, followup.lead_id)
            items.append(
                {
                    "followup_id": str(followup.id),
                    "lead_id": str(lead.id),
                    "lead": lead.title,
                    "scheduled_at": followup.scheduled_at.isoformat(),
                    "note": followup.note,
                    "status": followup.status,
                }
            )
        return {"data": {"followups": items, "count": len(items)}}

    async def _get_overdue_followups(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        followups = lead_followup_service.list_overdue_followups(
            session=self.session,
            current_user=self.current_user,
        )
        items = []
        for followup in followups:
            lead = lead_service.get_lead(self.session, self.current_user, followup.lead_id)
            items.append(
                {
                    "followup_id": str(followup.id),
                    "lead_id": str(lead.id),
                    "lead": lead.title,
                    "scheduled_at": followup.scheduled_at.isoformat(),
                    "note": followup.note,
                    "status": followup.status,
                }
            )
        return {"data": {"followups": items, "count": len(items)}}

    async def _get_lead_details(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        lead_id = self._require_uuid(args, "lead_id")
        lead = lead_service.get_lead(self.session, self.current_user, lead_id)
        return {
            "data": {
                "lead": {
                    "id": str(lead.id),
                    "title": lead.title,
                    "customer_id": str(lead.customer_id) if lead.customer_id else None,
                    "stage_id": str(lead.stage_id),
                    "source": lead.source,
                    "estimated_value": self._decimal_to_float(lead.estimated_value),
                    "follow_up_at": lead.follow_up_at.isoformat() if lead.follow_up_at else None,
                    "notes": lead.notes,
                    "created_at": lead.created_at.isoformat(),
                    "updated_at": lead.updated_at.isoformat(),
                }
            }
        }

    async def _get_customer_outstanding(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        customer_id = self._require_uuid(args, "customer_id")
        summary = customer_service.get_customer_outstanding(
            session=self.session,
            business_id=business_id,
            customer_id=customer_id,
        )
        return {"data": self._serialize_decimals(summary)}

    async def _list_customer_invoices(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        customer_id = self._require_uuid(args, "customer_id")
        status = self._optional_invoice_status(args.get("status"))
        invoices, total, summary = invoice_service.list_customer_invoices(
            session=self.session,
            current_user=self.current_user,
            customer_id=customer_id,
            status=status,
            limit=20,
            offset=0,
        )
        return {
            "data": {
                "invoices": [
                    {
                        "id": str(invoice.id),
                        "invoice_number": invoice.invoice_number,
                        "issued_date": invoice.issued_date.isoformat(),
                        "due_date": invoice.due_date.isoformat(),
                        "total_amount": self._decimal_to_float(invoice.total_amount),
                        "amount_paid": self._decimal_to_float(invoice.amount_paid),
                        "status": invoice.status.value,
                    }
                    for invoice in invoices
                ],
                "count": total,
                "summary": self._serialize_decimals(summary),
            }
        }

    async def _search_customer(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        query = self._require_str(args, "query")
        customers = customer_service.search_customers(
            session=self.session,
            current_user=self.current_user,
            query=query,
            limit=10,
        )
        return {
            "data": {
                "customers": [
                    {
                        "id": str(customer.id),
                        "name": customer.name,
                        "phone": customer.phone,
                        "email": customer.email,
                    }
                    for customer in customers
                ],
                "count": len(customers),
            }
        }

    async def _search_lead(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        query = self._require_str(args, "query")
        leads = lead_service.search_leads(
            session=self.session,
            current_user=self.current_user,
            query=query,
            limit=10,
        )
        return {
            "data": {
                "leads": [
                    {
                        "id": str(lead.id),
                        "title": lead.title,
                        "customer_name": lead.customer_name,
                        "customer_phone": lead.customer_phone,
                        "stage_name": lead.stage_name,
                        "estimated_value": self._decimal_to_float(lead.estimated_value),
                    }
                    for lead in leads
                ],
                "count": len(leads),
            }
        }

    async def _get_dashboard_summary(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        summary = dashboard_service.get_dashboard_summary(
            session=self.session,
            current_user=self.current_user,
        )
        return {"data": summary}

    async def _list_customer_payments(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        customer_id = self._require_uuid(args, "customer_id")
        payments = payment_service.list_customer_payments(
            session=self.session,
            current_user=self.current_user,
            customer_id=customer_id,
        )
        return {
            "data": {
                "payments": [
                    {
                        "id": str(payment.id),
                        "invoice_id": str(payment.invoice_id),
                        "amount": self._decimal_to_float(payment.amount),
                        "payment_method": payment.payment_method.value,
                        "payment_date": payment.payment_date.isoformat(),
                        "reference": payment.reference,
                    }
                    for payment in payments
                ],
                "count": len(payments),
            }
        }

    async def _get_lead_followups(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        lead_id = self._require_uuid(args, "lead_id")
        followups = lead_followup_service.list_followups(
            session=self.session,
            current_user=self.current_user,
            lead_id=lead_id,
        )
        return {
            "data": {
                "followups": [
                    {
                        "id": str(followup.id),
                        "scheduled_at": followup.scheduled_at.isoformat(),
                        "note": followup.note,
                        "status": followup.status,
                        "completed_at": followup.completed_at.isoformat() if followup.completed_at else None,
                    }
                    for followup in followups
                ],
                "count": len(followups),
            }
        }

    async def _get_catalog_items(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        items = catalog_item_service.list_catalog_items(
            session=self.session,
            current_user=self.current_user,
            search=args.get("search"),
            is_active=True,
        )
        return {
            "data": {
                "items": [
                    {
                        "id": str(item.id),
                        "name": item.name,
                        "description": item.description,
                        "unit": item.custom_unit or item.unit.value,
                        "default_rate": self._decimal_to_float(item.default_rate),
                        "gst_percent": self._decimal_to_float(item.gst_percent),
                    }
                    for item in items
                ],
                "count": len(items),
            }
        }

    async def _create_lead(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        stages = pipeline_service.get_stages_for_business(
            session=self.session,
            current_user=self.current_user,
        )
        default_stage = stages[0] if stages else None
        customer_id = args.get("customer_id")
        payload = {
            "name": self._require_str(args, "name"),
            "phone": self._require_str(args, "phone"),
            "requirement": args.get("requirement"),
            "source": args.get("source"),
            "estimated_value": args.get("estimated_value"),
            "notes": args.get("notes"),
            "customer_id": str(customer_id) if customer_id else None,
            "default_stage_id": str(default_stage.id) if default_stage else None,
            "default_stage_name": default_stage.name if default_stage else None,
        }
        return {
            "data": {"message": "Lead prepared for creation", "lead": payload},
            "action": {
                "type": "confirm_create_lead",
                "form_name": "create_lead",
                "prefilled_data": payload,
            },
        }

    async def _update_lead_stage(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        lead_id = self._require_uuid(args, "lead_id")
        stage_id = self._require_uuid(args, "stage_id")
        lead = lead_service.get_lead(self.session, self.current_user, lead_id)
        stages = pipeline_service.get_stages_for_business(
            session=self.session,
            current_user=self.current_user,
        )
        current_stage = next((item for item in stages if item.id == lead.stage_id), None)
        stage = next((item for item in stages if item.id == stage_id), None)
        if stage is None:
            raise ValueError("Stage not found for this business")

        payload = {
            "lead_id": str(lead.id),
            "lead_title": lead.title,
            "current_stage_id": str(lead.stage_id),
            "current_stage_name": current_stage.name if current_stage else None,
            "target_stage_id": str(stage.id),
            "target_stage_name": stage.name,
        }
        return {
            "data": {"message": "Lead stage update prepared", "update": payload},
            "action": {
                "type": "confirm_update_lead_stage",
                "form_name": "update_lead_stage",
                "prefilled_data": payload,
            },
        }

    async def _schedule_followup(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        lead_id = self._require_uuid(args, "lead_id")
        lead = lead_service.get_lead(self.session, self.current_user, lead_id)
        scheduled_date = self._parse_date(self._require_str(args, "scheduled_date"))
        note = args.get("note")
        payload = {
            "lead_id": str(lead.id),
            "lead_title": lead.title,
            "scheduled_date": scheduled_date.isoformat(),
            "scheduled_at": datetime.combine(scheduled_date, time(hour=9), tzinfo=timezone.utc).isoformat(),
            "note": note,
        }
        return {
            "data": {"message": "Follow-up prepared for scheduling", "followup": payload},
            "action": {
                "type": "confirm_schedule_followup",
                "form_name": "schedule_followup",
                "prefilled_data": payload,
            },
        }

    async def _prepare_invoice(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        customer_id = args.get("customer_id")
        customer_name = str(args.get("customer_name") or "").strip()
        lead_id = args.get("lead_id")
        due_date = str(args.get("due_date") or "").strip()
        notes = args.get("notes")
        raw_items = args.get("items") or []

        items: list[dict[str, Any]] = []
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")

        for index, item in enumerate(raw_items):
            quantity = Decimal(str(item.get("quantity", 1) or 1))
            rate = Decimal(str(item.get("rate", 0) or 0))
            gst_percent = Decimal(str(item.get("gst_percent", 18) or 18))
            description = str(item.get("description") or item.get("name") or "").strip()

            line_subtotal = quantity * rate
            line_tax = (line_subtotal * gst_percent) / Decimal("100")
            line_total = line_subtotal + line_tax

            subtotal += line_subtotal
            tax_total += line_tax

            items.append(
                {
                    "catalog_item_id": item.get("catalog_item_id"),
                    "name": str(item.get("name") or "").strip(),
                    "description": description,
                    "unit": str(item.get("unit") or "piece"),
                    "quantity": float(quantity),
                    "rate": float(rate),
                    "gst_percent": float(gst_percent),
                    "line_total": float(line_total.quantize(Decimal("0.01"))),
                    "sort_order": index + 1,
                }
            )

        if not due_date:
            due_date = (date.today() + timedelta(days=15)).isoformat()

        issued_date = date.today().isoformat()
        payload = {
            "customer_id": str(customer_id) if customer_id else None,
            "customer_name": customer_name,
            "lead_id": str(lead_id) if lead_id else None,
            "issued_date": issued_date,
            "due_date": due_date,
            "items": items,
            "notes": notes,
            "subtotal": float(subtotal.quantize(Decimal("0.01"))),
            "tax_total": float(tax_total.quantize(Decimal("0.01"))),
            "total_amount": float((subtotal + tax_total).quantize(Decimal("0.01"))),
        }
        return {
            "data": {"message": "Invoice prepared for review"},
            "action": {
                "type": "confirm_create_invoice",
                "form_name": "create_invoice",
                "prefilled_data": payload,
            },
        }

    async def _generate_invoice_pdf(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        invoice_id = self._require_uuid(args, "invoice_id")
        pdf_url = invoice_service.get_or_generate_pdf(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        invoice = invoice_service.get_invoice(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        return {
            "data": {
                "pdf_url": pdf_url,
                "invoice_id": str(invoice_id),
                "invoice_number": invoice.invoice_number,
                "message": f"PDF generated for {invoice.invoice_number}",
            }
        }

    async def _add_lead_note(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        lead_id = self._require_uuid(args, "lead_id")
        note = self._require_str(args, "note")
        lead = lead_service.get_lead(self.session, self.current_user, lead_id)
        payload = {
            "lead_id": str(lead_id),
            "lead_title": lead.title,
            "note": note,
        }
        return {
            "data": {"message": "Note prepared for review"},
            "action": {
                "type": "confirm_add_lead_note",
                "form_name": "add_lead_note",
                "prefilled_data": payload,
            },
        }

    def _apply_context_fallbacks(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context_type: str,
        context_id: UUID | None,
    ) -> dict[str, Any]:
        args = dict(tool_args)
        if context_id is None:
            return args

        if context_type == "customer" and tool_name in {
            "get_customer_outstanding",
            "list_customer_invoices",
            "list_customer_payments",
            "create_lead",
            "prepare_invoice",
        }:
            args.setdefault("customer_id", str(context_id))
            if tool_name == "prepare_invoice" and "customer_name" not in args:
                try:
                    customer = customer_service.get_customer(self.session, self.current_user, context_id)
                    args.setdefault("customer_name", customer.name)
                except HTTPException:
                    pass

        if context_type == "lead" and tool_name in {
            "get_lead_details",
            "get_lead_followups",
            "update_lead_stage",
            "schedule_followup",
            "prepare_invoice",
            "add_lead_note",
        }:
            args.setdefault("lead_id", str(context_id))

        if context_type == "lead" and tool_name == "prepare_invoice" and "customer_id" not in args:
            lead = lead_service.get_lead(self.session, self.current_user, context_id)
            if lead.customer_id is not None:
                args.setdefault("customer_id", str(lead.customer_id))
                try:
                    customer = customer_service.get_customer(self.session, self.current_user, lead.customer_id)
                    args.setdefault("customer_name", customer.name)
                except HTTPException:
                    pass

        return args

    def _require_uuid(self, args: dict[str, Any], key: str) -> UUID:
        value = args.get(key)
        if value is None:
            raise ValueError(f"{key} is required")
        return UUID(str(value))

    def _require_str(self, args: dict[str, Any], key: str) -> str:
        value = args.get(key)
        if value is None:
            raise ValueError(f"{key} is required")
        text = str(value).strip()
        if not text:
            raise ValueError(f"{key} is required")
        return text

    def _parse_date(self, value: str) -> datetime.date:
        return datetime.strptime(value, "%Y-%m-%d").date()

    def _optional_invoice_status(self, value: Any) -> InvoiceStatus | None:
        if value is None:
            return None
        return InvoiceStatus(str(value))

    def _decimal_to_float(self, value: Decimal | None) -> float | None:
        if value is None:
            return None
        return float(value)

    def _serialize_decimals(self, data: dict[str, Any]) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, Decimal):
                serialized[key] = float(value)
            else:
                serialized[key] = value
        return serialized


def build_tool_user(business_id: UUID) -> User:
    return User(
        business_id=business_id,
        name="AI Assistant",
        email="ai-assistant@example.com",
        role=UserRole.OWNER,
    )
