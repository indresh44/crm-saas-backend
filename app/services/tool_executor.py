from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.models.enums import InvoiceStatus, PaymentMethod, UserRole
from app.models.lead import LeadCreate
from app.models.lead_followup import LeadFollowupCreate
from app.models.payment import PaymentCreate
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
        "get_invoice_details",
        "list_customers_by_outstanding",
        "list_leads_by_stage",
        "get_pipeline_summary",
        "list_overdue_invoices",
        "get_unpaid_invoice_summary",
        "get_recent_payments",
        "get_revenue_summary",
    }

    WRITE_TOOLS = {
        "create_lead",
        "update_lead_stage",
        "schedule_followup",
        "prepare_invoice",
        "add_lead_note",
        "record_payment",
        "send_payment_reminder",
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

    async def _get_invoice_details(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        invoice_id = self._require_uuid(args, "invoice_id")
        invoice = invoice_service.get_invoice(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        items = invoice_service.list_invoice_items(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        payments = payment_service.list_payments(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )

        customer_name, lead_title = self._get_invoice_display_context(invoice.lead_id)
        amount_paid = sum((payment.amount for payment in payments), Decimal("0"))
        balance_due = max(invoice.total_amount - amount_paid, Decimal("0"))
        item_lines = [
            (
                f"- {(item.name or item.description)}: "
                f"{float(item.quantity):g} x Rs {float(item.unit_price):,.0f}/{item.unit} "
                f"(GST {float(item.gst_percent):g}%) = Rs {float(item.amount):,.0f}"
            )
            for item in items
        ]

        return {
            "data": {
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
                "status": invoice.status.value,
                "issued_date": invoice.issued_date.isoformat(),
                "due_date": invoice.due_date.isoformat(),
                "customer_name": customer_name,
                "lead_title": lead_title,
                "subtotal": self._decimal_to_float(invoice.subtotal) or 0,
                "tax_total": self._decimal_to_float(invoice.tax_total) or 0,
                "total_amount": self._decimal_to_float(invoice.total_amount) or 0,
                "amount_paid": self._decimal_to_float(amount_paid) or 0,
                "balance_due": self._decimal_to_float(balance_due) or 0,
                "items": "\n".join(item_lines) if item_lines else "No items",
                "items_count": len(item_lines),
                "pdf_url": invoice.pdf_url,
            }
        }

    async def _record_payment(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        invoice_id = self._require_uuid(args, "invoice_id")
        amount = float(args.get("amount", 0) or 0)
        invoice = invoice_service.get_invoice(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        customer_name, _ = self._get_invoice_display_context(invoice.lead_id)
        payments = payment_service.list_payments(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        amount_paid = sum((payment.amount for payment in payments), Decimal("0"))
        balance_due = max(invoice.total_amount - amount_paid, Decimal("0"))

        if amount <= 0:
            raise ValueError("Payment amount must be positive")

        payment_date = str(args.get("payment_date") or "").strip() or date.today().isoformat()
        payload = {
            "invoice_id": str(invoice.id),
            "invoice_number": str(args.get("invoice_number") or invoice.invoice_number),
            "customer_name": customer_name,
            "amount": amount,
            "payment_method": str(args.get("payment_method") or "upi"),
            "reference": str(args.get("reference") or ""),
            "payment_date": payment_date,
            "notes": str(args.get("notes") or ""),
            "total_amount": self._decimal_to_float(invoice.total_amount) or 0,
            "amount_already_paid": self._decimal_to_float(amount_paid) or 0,
            "balance_due": self._decimal_to_float(balance_due) or 0,
        }
        return {
            "data": {"message": "Payment prepared for review"},
            "action": {
                "type": "confirm_record_payment",
                "form_name": "record_payment",
                "prefilled_data": payload,
            },
        }

    async def _list_customers_by_outstanding(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        limit = max(1, int(args.get("limit", 10) or 10))
        customers = customer_service.list_customers(
            session=self.session,
            current_user=self.current_user,
        )

        customer_data: list[dict[str, Any]] = []
        for customer in customers:
            summary = customer_service.get_customer_outstanding(
                session=self.session,
                business_id=self.current_user.business_id,
                customer_id=customer.id,
            )
            outstanding = float(summary.get("outstanding", 0) or 0)
            if outstanding > 0:
                customer_data.append(
                    {
                        "customer_id": str(customer.id),
                        "name": customer.name,
                        "phone": customer.phone,
                        "outstanding": outstanding,
                    }
                )

        customer_data.sort(key=lambda item: item["outstanding"], reverse=True)
        top_customers = customer_data[:limit]
        lines = [
            f"{index}. {customer['name']} ({customer['phone']}) - Rs {customer['outstanding']:,.0f}"
            for index, customer in enumerate(top_customers, 1)
        ]
        return {
            "data": {
                "customers": "\n".join(lines) if lines else "No customers with outstanding balance",
                "count": len(top_customers),
                "total_outstanding": sum(customer["outstanding"] for customer in top_customers),
            }
        }

    async def _list_leads_by_stage(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        stage_name = self._require_str(args, "stage_name")
        limit = max(1, int(args.get("limit", 20) or 20))
        stages = pipeline_service.get_stages_for_business(
            session=self.session,
            current_user=self.current_user,
        )
        stage = next((item for item in stages if item.name.lower() == stage_name.lower()), None)
        if stage is None:
            available = ", ".join(item.name for item in stages)
            return {"data": {"error": f"Stage '{stage_name}' not found. Available: {available}"}}

        leads = [
            lead
            for lead in lead_service.list_leads(self.session, self.current_user)
            if lead.stage_name and lead.stage_name.lower() == stage.name.lower()
        ][:limit]
        total_value = 0.0
        lines: list[str] = []
        for lead in leads:
            value = float(lead.estimated_value or 0)
            total_value += value
            lines.append(
                f"- {lead.title} ({lead.customer_name or 'Unknown'}) - ₹{value:,.0f} · "
                f"{lead.created_at.date().isoformat() if lead.created_at else ''}"
            )
        return {
            "data": {
                "stage": stage.name,
                "count": len(leads),
                "total_value": total_value,
                "leads": "\n".join(lines) if lines else "No leads in this stage",
            }
        }

    async def _get_pipeline_summary(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id, args
        stages = pipeline_service.get_stages_for_business(
            session=self.session,
            current_user=self.current_user,
        )
        leads = lead_service.list_leads(self.session, self.current_user)
        lines: list[str] = []
        total_leads = 0
        total_value = 0.0
        for stage in stages:
            stage_leads = [lead for lead in leads if lead.stage_name and lead.stage_name.lower() == stage.name.lower()]
            count = len(stage_leads)
            value = sum(float(lead.estimated_value or 0) for lead in stage_leads)
            total_leads += count
            total_value += value
            lines.append(f"- {stage.name}: {count} leads - ₹{value:,.0f}")
        return {
            "data": {
                "pipeline": "\n".join(lines) if lines else "No pipeline stages found",
                "total_leads": total_leads,
                "total_pipeline_value": total_value,
            }
        }

    async def _list_overdue_invoices(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        limit = max(1, int(args.get("limit", 20) or 20))
        today = date.today()
        invoices, _, _ = invoice_service.list_invoices(
            session=self.session,
            current_user=self.current_user,
            limit=200,
            offset=0,
        )
        overdue_lines: list[str] = []
        total_overdue = 0.0
        for invoice in invoices:
            amount_paid = float(invoice.amount_paid or 0)
            balance = float(invoice.total_amount or 0) - amount_paid
            if balance <= 0 or invoice.status in {InvoiceStatus.PAID, InvoiceStatus.DRAFT} or invoice.due_date >= today:
                continue
            days_overdue = (today - invoice.due_date).days
            total_overdue += balance
            overdue_lines.append(
                f"- {invoice.invoice_number} · {invoice.customer_name or 'Unknown'} · ₹{balance:,.0f} "
                f"· {days_overdue} days overdue (due: {invoice.due_date.isoformat()})"
            )
            if len(overdue_lines) >= limit:
                break
        return {
            "data": {
                "invoices": "\n".join(overdue_lines) if overdue_lines else "No overdue invoices!",
                "count": len(overdue_lines),
                "total_overdue_amount": total_overdue,
            }
        }

    async def _get_unpaid_invoice_summary(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id, args
        today = date.today()
        invoices, _, _ = invoice_service.list_invoices(
            session=self.session,
            current_user=self.current_user,
            limit=500,
            offset=0,
        )
        total_invoiced = 0.0
        total_paid = 0.0
        total_outstanding = 0.0
        overdue_count = 0
        unpaid_count = 0
        for invoice in invoices:
            if invoice.status == InvoiceStatus.DRAFT:
                continue
            amount = float(invoice.total_amount or 0)
            paid = float(invoice.amount_paid or 0)
            balance = amount - paid
            total_invoiced += amount
            total_paid += paid
            if balance > 0:
                unpaid_count += 1
                total_outstanding += balance
                if invoice.due_date < today:
                    overdue_count += 1
        return {
            "data": {
                "total_invoiced": total_invoiced,
                "total_collected": total_paid,
                "total_outstanding": total_outstanding,
                "unpaid_invoice_count": unpaid_count,
                "overdue_count": overdue_count,
                "collection_rate": round((total_paid / total_invoiced * 100), 1) if total_invoiced > 0 else 0,
            }
        }

    async def _get_recent_payments(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        days = max(1, int(args.get("days", 7) or 7))
        limit = max(1, int(args.get("limit", 20) or 20))
        since = date.today() - timedelta(days=days)
        payments = [
            payment
            for payment in payment_service.list_payments(self.session, self.current_user)
            if payment.payment_date >= since
        ][:limit]
        total = 0.0
        lines: list[str] = []
        for payment in payments:
            amount = float(payment.amount)
            total += amount
            try:
                invoice = invoice_service.get_invoice(self.session, self.current_user, payment.invoice_id)
                customer_name, _ = self._get_invoice_display_context(invoice.lead_id)
                invoice_number = invoice.invoice_number
            except HTTPException:
                customer_name = ""
                invoice_number = ""
            lines.append(
                f"- ₹{amount:,.0f} · {payment.payment_method.value} · {customer_name or 'Unknown'} "
                f"· {invoice_number} · {payment.payment_date.isoformat()}"
            )
        period_label = "today" if days == 1 else f"last {days} days"
        return {
            "data": {
                "payments": "\n".join(lines) if lines else f"No payments in {period_label}",
                "count": len(lines),
                "total_received": total,
                "period": period_label,
            }
        }

    async def _get_revenue_summary(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        period = str(args.get("period") or "this_month")
        today = date.today()
        if period == "today":
            start_date = end_date = today
            label = "Today"
        elif period == "this_week":
            start_date = today - timedelta(days=today.weekday())
            end_date = today
            label = "This week"
        elif period == "last_month":
            first_of_this_month = today.replace(day=1)
            end_date = first_of_this_month - timedelta(days=1)
            start_date = end_date.replace(day=1)
            label = f"Last month ({start_date.strftime('%B')})"
        elif period == "last_30_days":
            start_date = today - timedelta(days=30)
            end_date = today
            label = "Last 30 days"
        elif period == "last_90_days":
            start_date = today - timedelta(days=90)
            end_date = today
            label = "Last 90 days"
        else:
            start_date = today.replace(day=1)
            end_date = today
            label = "This month"

        payments = [
            payment
            for payment in payment_service.list_payments(self.session, self.current_user)
            if start_date <= payment.payment_date <= end_date
        ]
        total_revenue = sum(float(payment.amount) for payment in payments)
        return {
            "data": {
                "period": label,
                "total_revenue": total_revenue,
                "payment_count": len(payments),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }
        }

    async def _send_payment_reminder(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        customer_name = str(args.get("customer_name") or "")
        customer_phone = str(args.get("customer_phone") or "")
        outstanding = float(args.get("outstanding_amount", 0) or 0)
        invoice_numbers = str(args.get("invoice_numbers") or "")
        tone = str(args.get("message_tone") or "polite")
        if tone == "firm":
            message = (
                f"Namaste {customer_name} ji,\n\n"
                f"Aapke account mein ₹{outstanding:,.0f} ka outstanding amount hai"
                f"{' (Invoice: ' + invoice_numbers + ')' if invoice_numbers else ''}.\n\n"
                f"Kripya jaldi se jaldi payment karein. Agar koi issue hai toh humse baat karein.\n\n"
                f"Dhanyavaad."
            )
        elif tone == "urgent":
            message = (
                f"{customer_name} ji,\n\n"
                f"Aapka ₹{outstanding:,.0f} ka payment kaafi din se pending hai"
                f"{' (' + invoice_numbers + ')' if invoice_numbers else ''}.\n\n"
                f"Kripya aaj hi payment karein. Yeh final reminder hai.\n\n"
                f"Dhanyavaad."
            )
        else:
            message = (
                f"Namaste {customer_name} ji,\n\n"
                f"Yeh ek friendly reminder hai ki aapka ₹{outstanding:,.0f} ka payment pending hai"
                f"{' (' + invoice_numbers + ')' if invoice_numbers else ''}.\n\n"
                f"Agar payment ho chuki hai toh please ignore karein.\n\n"
                f"Dhanyavaad!"
            )
        payload = {
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "outstanding_amount": outstanding,
            "invoice_numbers": invoice_numbers,
            "message": message,
            "tone": tone,
            "whatsapp_url": f"https://wa.me/91{customer_phone}?text={quote(message)}",
        }
        return {
            "data": {"message": "Payment reminder prepared"},
            "action": {
                "type": "confirm_send_payment_reminder",
                "form_name": "send_payment_reminder",
                "prefilled_data": payload,
            },
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

    def _get_invoice_display_context(self, lead_id: UUID | None) -> tuple[str | None, str | None]:
        if lead_id is None:
            return None, None

        try:
            lead = lead_service.get_lead(self.session, self.current_user, lead_id)
        except HTTPException:
            return None, None

        customer_name = None
        if lead.customer_id is not None:
            try:
                customer = customer_service.get_customer(
                    self.session,
                    self.current_user,
                    lead.customer_id,
                )
                customer_name = customer.name
            except HTTPException:
                customer_name = None
        return customer_name, lead.title

    def _optional_payment_method(self, value: Any) -> PaymentMethod:
        if value is None or value == "":
            return PaymentMethod.UPI
        return PaymentMethod(str(value))

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
