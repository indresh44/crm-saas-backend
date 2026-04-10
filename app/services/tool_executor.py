from __future__ import annotations

from collections import defaultdict
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
from app.models.lead_followup import LeadFollowupCreate, LeadFollowupUpdate
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
        "get_customer_details",
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
        "get_stale_followups",
        "query_invoices",
        "get_billing_analytics",
        "get_invoice_payment_history",
        "set_onboarding_persona",
        "add_onboarding_catalog_item",
        "complete_onboarding",
    }

    WRITE_TOOLS = {
        "create_lead",
        "update_lead_stage",
        "schedule_followup",
        "prepare_invoice",
        "add_lead_note",
        "record_payment",
        "send_payment_reminder",
        "complete_followup",
        "reschedule_followup",
        "bulk_update_followups",
        "update_invoice",
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

        stage_name = None
        if lead.stage_id:
            stages = pipeline_service.get_stages_for_business(self.session, self.current_user)
            stage = next((s for s in stages if s.id == lead.stage_id), None)
            stage_name = stage.name if stage else None

        customer_name = None
        customer_phone = None
        if lead.customer_id:
            try:
                customer = customer_service.get_customer(self.session, self.current_user, lead.customer_id)
                customer_name = customer.name
                customer_phone = customer.phone
            except Exception:
                pass

        return {
            "data": {
                "lead": {
                    "id": str(lead.id),
                    "title": lead.title,
                    "customer_id": str(lead.customer_id) if lead.customer_id else None,
                    "customer_name": customer_name,
                    "customer_phone": customer_phone,
                    "stage_id": str(lead.stage_id),
                    "stage_name": stage_name,
                    "source": lead.source,
                    "estimated_value": self._decimal_to_float(lead.estimated_value),
                    "follow_up_at": lead.follow_up_at.isoformat() if lead.follow_up_at else None,
                    "notes": lead.notes,
                    "created_at": lead.created_at.isoformat(),
                    "updated_at": lead.updated_at.isoformat(),
                }
            }
        }

    async def _get_customer_details(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        customer_id = self._require_uuid(args, "customer_id")
        customer = customer_service.get_customer(self.session, self.current_user, customer_id)
        return {
            "data": {
                "customer": {
                    "id": str(customer.id),
                    "name": customer.name,
                    "phone": customer.phone,
                    "email": getattr(customer, "email", None),
                    "created_at": customer.created_at.isoformat() if customer.created_at else None,
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
        invoices, _total, summary = invoice_service.list_customer_invoices(
            session=self.session,
            current_user=self.current_user,
            customer_id=customer_id,
            status=status,
            limit=20,
            offset=0,
            exclude_draft=status is None,
        )
        visible = invoices
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
                    for invoice in visible
                ],
                "count": len(visible),
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

    async def _get_stale_followups(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        older_than_days = max(1, int(args.get("older_than_days", 7) or 7))
        customer_id = self._optional_uuid(args.get("customer_id"))
        lead_id = self._optional_uuid(args.get("lead_id"))
        limit = max(1, int(args.get("limit", 30) or 30))

        followups = lead_followup_service.list_followups(
            session=self.session,
            current_user=self.current_user,
            lead_id=lead_id,
            customer_id=customer_id,
            status="pending",
            older_than_days=older_than_days,
            limit=limit,
        )

        lines: list[str] = []
        items: list[dict[str, Any]] = []
        today = date.today()
        for followup in followups:
            customer_name, lead_title = self._get_followup_display_context(followup.lead_id)
            days_old = max(0, (today - followup.scheduled_at.date()).days)
            lines.append(
                f"- {lead_title or 'Untitled'} ({customer_name or 'Unknown'}) · "
                f"{days_old} days old · scheduled: {followup.scheduled_at.date().isoformat()}"
            )
            items.append(
                {
                    "followup_id": str(followup.id),
                    "lead_id": str(followup.lead_id),
                    "lead": lead_title,
                    "customer_name": customer_name,
                    "scheduled_at": followup.scheduled_at.isoformat(),
                    "note": followup.note,
                    "status": followup.status,
                }
            )

        return {
            "data": {
                "followups": "\n".join(lines) if lines else f"No stale follow-ups older than {older_than_days} days",
                "items": items,
                "count": len(items),
                "older_than_days": older_than_days,
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
                        "sac_code": item.sac_code,
                    }
                    for item in items
                ],
                "count": len(items),
            }
        }

    async def _query_invoices(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        period = str(args.get("period") or "").strip() or None
        from_date, to_date = self._resolve_query_period(
            period=period,
            from_value=args.get("from_date"),
            to_value=args.get("to_date"),
        )
        customer_id = self._optional_uuid(args.get("customer_id"))
        lead_id = self._optional_uuid(args.get("lead_id"))
        status_filter = self._optional_invoice_status(args.get("status"))
        limit = max(1, int(args.get("limit", 20) or 20))
        fetch_limit = min(max(limit * 5, 100), 300)

        invoices, _, _ = invoice_service.list_invoices(
            session=self.session,
            current_user=self.current_user,
            customer_id=customer_id,
            status=status_filter,
            from_date=from_date,
            to_date=to_date,
            lead_id=lead_id,
            limit=fetch_limit,
            offset=0,
            exclude_draft=status_filter is None,
        )

        min_amount = self._optional_decimal(args.get("min_amount"))
        max_amount = self._optional_decimal(args.get("max_amount"))
        payment_status = str(args.get("payment_status") or "").strip() or None
        search = str(args.get("search") or "").strip().lower()

        filtered = []
        for invoice in invoices:
            total_amount = float(invoice.total_amount or 0)
            amount_paid = float(invoice.amount_paid or 0)
            balance_due = max(total_amount - amount_paid, 0)

            if min_amount is not None and total_amount < float(min_amount):
                continue
            if max_amount is not None and total_amount > float(max_amount):
                continue

            if payment_status == "unpaid" and amount_paid > 0:
                continue
            if payment_status == "partially_paid" and (amount_paid <= 0 or amount_paid >= total_amount):
                continue
            if payment_status == "fully_paid" and amount_paid < total_amount:
                continue

            if search and not self._invoice_matches_search(invoice.id, invoice, search):
                continue

            filtered.append((invoice, total_amount, amount_paid, balance_due))

        sort_by = str(args.get("sort_by") or "date")
        if sort_by == "amount":
            filtered.sort(key=lambda row: row[1], reverse=True)
        elif sort_by == "due_date":
            filtered.sort(key=lambda row: (row[0].due_date is None, row[0].due_date or date.max))
        elif sort_by == "outstanding":
            filtered.sort(key=lambda row: row[3], reverse=True)
        else:
            filtered.sort(key=lambda row: row[0].issued_date, reverse=True)

        total_billed = 0.0
        total_collected = 0.0
        total_pending = 0.0
        overdue_count = 0
        today = date.today()
        for invoice, total_amount, amount_paid, balance_due in filtered:
            total_billed += total_amount
            total_collected += amount_paid
            total_pending += balance_due
            if balance_due > 0 and invoice.due_date < today:
                overdue_count += 1

        limited_rows = filtered[:limit]
        lines: list[str] = []
        items: list[dict[str, Any]] = []
        for invoice, total_amount, amount_paid, balance_due in limited_rows:
            line = (
                f"- {invoice.invoice_number} · {invoice.customer_name or 'Unknown'} · ₹{total_amount:,.0f} "
                f"(paid: ₹{amount_paid:,.0f}, pending: ₹{balance_due:,.0f}) · {invoice.status.value}"
            )
            if invoice.due_date:
                line += f" · due: {invoice.due_date.isoformat()}"
            lines.append(line)
            items.append(
                {
                    "invoice_id": str(invoice.id),
                    "invoice_number": invoice.invoice_number,
                    "customer_name": invoice.customer_name,
                    "lead_title": invoice.lead_title,
                    "issued_date": invoice.issued_date.isoformat(),
                    "due_date": invoice.due_date.isoformat(),
                    "total_amount": total_amount,
                    "amount_paid": amount_paid,
                    "balance_due": balance_due,
                    "status": invoice.status.value,
                }
            )

        return {
            "data": {
                "invoices": "\n".join(lines) if lines else "No invoices found matching these filters.",
                "items": items,
                "count": len(filtered),
                "total_billed": round(total_billed, 2),
                "total_collected": round(total_collected, 2),
                "total_pending": round(total_pending, 2),
                "overdue_count": overdue_count,
                "average_invoice_value": round(total_billed / len(filtered), 2) if filtered else 0,
                "filters_applied": {
                    key: value
                    for key, value in {
                        "period": period,
                        "from_date": from_date.isoformat() if from_date else None,
                        "to_date": to_date.isoformat() if to_date else None,
                        "status": status_filter.value if status_filter else None,
                        "customer_id": str(customer_id) if customer_id else None,
                        "lead_id": str(lead_id) if lead_id else None,
                        "payment_status": payment_status,
                        "search": search or None,
                        "min_amount": float(min_amount) if min_amount is not None else None,
                        "max_amount": float(max_amount) if max_amount is not None else None,
                    }.items()
                    if value is not None
                },
            }
        }

    async def _update_invoice(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        invoice_id = self._require_uuid(args, "invoice_id")
        invoice = invoice_service.get_invoice(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )

        if invoice.status == InvoiceStatus.PAID:
            raise ValueError("Paid invoices cannot be updated")

        items = invoice_service.list_invoice_items(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )

        current_items: list[dict[str, Any]] = []
        for item in items:
            current_items.append(
                {
                    "catalog_item_id": str(item.catalog_item_id) if item.catalog_item_id else None,
                    "name": item.name,
                    "description": item.description,
                    "quantity": float(item.quantity),
                    "rate": float(item.unit_price),
                    "unit": item.unit or "piece",
                    "gst_percent": float(item.gst_percent),
                    "sac_code": item.sac_code,
                    "line_total": float(item.amount + ((item.amount * item.gst_percent) / Decimal("100"))),
                }
            )

        changes: dict[str, Any] = {}
        new_status = str(args.get("new_status") or "").strip() or None
        if new_status:
            allowed_transitions = {
                InvoiceStatus.DRAFT: {InvoiceStatus.SENT.value, InvoiceStatus.APPROVED.value},
                InvoiceStatus.SENT: {InvoiceStatus.APPROVED.value},
            }
            if new_status not in allowed_transitions.get(invoice.status, set()):
                raise ValueError(
                    f"Cannot change status from '{invoice.status.value}' to '{new_status}'. "
                    f"Use 'approved' when client approves the estimate."
                )
            changes["new_status"] = new_status

        new_due_date = str(args.get("new_due_date") or "").strip() or None
        if new_due_date:
            changes["new_due_date"] = new_due_date

        add_items = list(args.get("add_items") or [])
        update_items = list(args.get("update_items") or [])
        remove_item_names = [str(name) for name in (args.get("remove_item_names") or []) if str(name).strip()]
        has_item_changes = bool(add_items or update_items or remove_item_names)
        if has_item_changes and invoice.status != InvoiceStatus.DRAFT:
            raise ValueError("Line items can only be modified on draft invoices")

        proposed_items = [dict(item) for item in current_items]

        if remove_item_names:
            remove_names = {name.lower() for name in remove_item_names}
            before_count = len(proposed_items)
            proposed_items = [item for item in proposed_items if item["name"].lower() not in remove_names]
            if len(proposed_items) == before_count:
                raise ValueError("No matching invoice items found to remove")
            changes["removed_items"] = remove_item_names

        if update_items:
            updated_labels: list[str] = []
            for update in update_items:
                target_name = str(update.get("item_name") or "").strip().lower()
                target_index = update.get("item_index")
                matched_index = None
                for index, item in enumerate(proposed_items):
                    if target_name and item["name"].lower() == target_name:
                        matched_index = index
                        break
                    if target_index is not None and index == int(target_index):
                        matched_index = index
                        break

                if matched_index is None:
                    raise ValueError("Invoice item to update was not found")

                item = proposed_items[matched_index]
                if "new_rate" in update and update.get("new_rate") is not None:
                    item["rate"] = float(update["new_rate"])
                if "new_quantity" in update and update.get("new_quantity") is not None:
                    item["quantity"] = float(update["new_quantity"])
                if "new_name" in update and update.get("new_name"):
                    item["name"] = str(update["new_name"])
                if "new_unit" in update and update.get("new_unit"):
                    item["unit"] = str(update["new_unit"])
                if "new_gst_percent" in update and update.get("new_gst_percent") is not None:
                    item["gst_percent"] = float(update["new_gst_percent"])
                if "new_description" in update and update.get("new_description"):
                    item["description"] = str(update["new_description"])

                line_subtotal = item["quantity"] * item["rate"]
                item["line_total"] = round(line_subtotal * (1 + item["gst_percent"] / 100), 2)
                updated_labels.append(item.get("name") or str(target_index))

            changes["updated_items"] = updated_labels

        if add_items:
            added_labels: list[str] = []
            for item in add_items:
                quantity = float(item.get("quantity", 1) or 1)
                rate = float(item.get("rate", 0) or 0)
                gst_percent = float(item.get("gst_percent", 18) or 18)
                line_total = round(quantity * rate * (1 + gst_percent / 100), 2)
                proposed_item = {
                    "catalog_item_id": item.get("catalog_item_id"),
                    "name": str(item.get("name") or "").strip(),
                    "description": str(item.get("description") or item.get("name") or "").strip(),
                    "quantity": quantity,
                    "rate": rate,
                    "unit": str(item.get("unit") or "piece"),
                    "gst_percent": gst_percent,
                    "line_total": line_total,
                }
                proposed_items.append(proposed_item)
                added_labels.append(proposed_item["name"])
            changes["added_items"] = added_labels

        proposed_subtotal = None
        proposed_tax = None
        proposed_total = None
        if has_item_changes:
            proposed_subtotal = round(sum(item["quantity"] * item["rate"] for item in proposed_items), 2)
            proposed_tax = round(
                sum(item["quantity"] * item["rate"] * item["gst_percent"] / 100 for item in proposed_items),
                2,
            )
            proposed_total = round(proposed_subtotal + proposed_tax, 2)

        payload = {
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "current_status": invoice.status.value,
            "current_due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "current_total": float(invoice.total_amount or 0),
            "changes": changes,
            "proposed_items": proposed_items if has_item_changes else None,
            "proposed_subtotal": proposed_subtotal,
            "proposed_tax": proposed_tax,
            "proposed_total": proposed_total,
        }

        return {
            "data": {"message": "Invoice update prepared for review"},
            "action": {
                "type": "confirm_update_invoice",
                "form_name": "update_invoice",
                "prefilled_data": payload,
            },
        }

    async def _get_billing_analytics(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        period = self._require_str(args, "period")
        compare_with = str(args.get("compare_with") or "").strip() or None
        group_by = str(args.get("group_by") or "").strip() or None
        limit = max(1, int(args.get("limit", 10) or 10))
        start_date, end_date = self._resolve_analytics_period(period)

        invoices, _, _ = invoice_service.list_invoices(
            session=self.session,
            current_user=self.current_user,
            from_date=start_date,
            to_date=end_date,
            limit=500,
            offset=0,
        )

        today = date.today()
        total_billed = 0.0
        total_collected = 0.0
        total_pending = 0.0
        overdue_count = 0
        status_breakdown: dict[str, int] = defaultdict(int)

        for invoice in invoices:
            total_amount = float(invoice.total_amount or 0)
            amount_paid = float(invoice.amount_paid or 0)
            balance_due = max(total_amount - amount_paid, 0)
            total_billed += total_amount
            total_collected += amount_paid
            total_pending += balance_due
            status_breakdown[invoice.status.value] += 1
            if balance_due > 0 and invoice.due_date < today:
                overdue_count += 1

        data: dict[str, Any] = {
            "period": period,
            "period_range": f"{start_date.isoformat()} to {end_date.isoformat()}",
            "total_invoices": len(invoices),
            "total_billed": round(total_billed, 2),
            "total_collected": round(total_collected, 2),
            "total_pending": round(total_pending, 2),
            "average_invoice_value": round(total_billed / len(invoices), 2) if invoices else 0,
            "collection_rate_percent": round((total_collected / total_billed) * 100, 1) if total_billed > 0 else 0,
            "overdue_count": overdue_count,
            "status_breakdown": dict(status_breakdown),
        }

        if compare_with == "previous_period":
            duration_days = (end_date - start_date).days
            previous_end = start_date - timedelta(days=1)
            previous_start = previous_end - timedelta(days=duration_days)
            previous_invoices, _, _ = invoice_service.list_invoices(
                session=self.session,
                current_user=self.current_user,
                from_date=previous_start,
                to_date=previous_end,
                limit=500,
                offset=0,
            )
            previous_billed = sum(float(invoice.total_amount or 0) for invoice in previous_invoices)
            previous_collected = sum(float(invoice.amount_paid or 0) for invoice in previous_invoices)
            data["comparison"] = {
                "previous_period": f"{previous_start.isoformat()} to {previous_end.isoformat()}",
                "prev_total_billed": round(previous_billed, 2),
                "prev_total_collected": round(previous_collected, 2),
                "prev_invoice_count": len(previous_invoices),
                "billed_change_percent": round(((total_billed - previous_billed) / previous_billed) * 100, 1)
                if previous_billed > 0
                else 0,
                "collected_change_percent": round(((total_collected - previous_collected) / previous_collected) * 100, 1)
                if previous_collected > 0
                else 0,
            }

        if group_by == "customer":
            customer_totals: dict[str, dict[str, Any]] = defaultdict(
                lambda: {"name": "Unknown", "billed": 0.0, "collected": 0.0, "count": 0}
            )
            for invoice in invoices:
                customer_key = str(getattr(invoice, "customer_name", None) or getattr(invoice, "id"))
                customer_totals[customer_key]["name"] = invoice.customer_name or "Unknown"
                customer_totals[customer_key]["billed"] += float(invoice.total_amount or 0)
                customer_totals[customer_key]["collected"] += float(invoice.amount_paid or 0)
                customer_totals[customer_key]["count"] += 1

            top_customers = sorted(customer_totals.values(), key=lambda item: item["billed"], reverse=True)[:limit]
            data["grouped_by"] = "customer"
            data["top_entries"] = "\n".join(
                f"{index}. {customer['name']} — ₹{customer['billed']:,.0f} billed, ₹{customer['collected']:,.0f} collected ({customer['count']} invoices)"
                for index, customer in enumerate(top_customers, 1)
            )

        elif group_by == "item":
            item_totals: dict[str, dict[str, float]] = defaultdict(lambda: {"revenue": 0.0, "quantity": 0.0})
            for invoice in invoices:
                invoice_items = invoice_service.list_invoice_items(
                    session=self.session,
                    current_user=self.current_user,
                    invoice_id=invoice.id,
                )
                for item in invoice_items:
                    revenue = float(item.amount + ((item.amount * item.gst_percent) / Decimal("100")))
                    item_totals[item.name]["revenue"] += revenue
                    item_totals[item.name]["quantity"] += float(item.quantity or 0)

            top_items = sorted(item_totals.items(), key=lambda item: item[1]["revenue"], reverse=True)[:limit]
            data["grouped_by"] = "item"
            data["top_entries"] = "\n".join(
                f"{index}. {name} — ₹{values['revenue']:,.0f} ({values['quantity']:,.0f} units)"
                for index, (name, values) in enumerate(top_items, 1)
            )

        elif group_by == "month":
            month_totals: dict[str, dict[str, float]] = defaultdict(lambda: {"billed": 0.0, "collected": 0.0, "count": 0})
            for invoice in invoices:
                month_key = invoice.issued_date.strftime("%Y-%m")
                month_totals[month_key]["billed"] += float(invoice.total_amount or 0)
                month_totals[month_key]["collected"] += float(invoice.amount_paid or 0)
                month_totals[month_key]["count"] += 1

            data["grouped_by"] = "month"
            data["breakdown"] = "\n".join(
                f"- {month}: ₹{values['billed']:,.0f} billed, ₹{values['collected']:,.0f} collected ({int(values['count'])} invoices)"
                for month, values in sorted(month_totals.items())
            )

        elif group_by == "status":
            data["grouped_by"] = "status"
            data["breakdown"] = "\n".join(
                f"- {status}: {count} invoices"
                for status, count in sorted(status_breakdown.items(), key=lambda row: row[1], reverse=True)
            )

        return {"data": data}

    async def _get_invoice_payment_history(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        invoice_id = self._require_uuid(args, "invoice_id")
        invoice = invoice_service.get_invoice(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        payments = payment_service.list_payments(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        payments.sort(key=lambda payment: (payment.payment_date, payment.created_at), reverse=True)

        total_paid = 0.0
        lines: list[str] = []
        items: list[dict[str, Any]] = []
        for payment in payments:
            amount = float(payment.amount)
            total_paid += amount
            line = f"- ₹{amount:,.0f} · {payment.payment_method.value} · {payment.payment_date.isoformat()}"
            if payment.reference:
                line += f" · Ref: {payment.reference}"
            lines.append(line)
            items.append(
                {
                    "payment_id": str(payment.id),
                    "amount": amount,
                    "payment_method": payment.payment_method.value,
                    "payment_date": payment.payment_date.isoformat(),
                    "reference": payment.reference,
                }
            )

        total_amount = float(invoice.total_amount or 0)
        return {
            "data": {
                "invoice_id": str(invoice_id),
                "invoice_number": invoice.invoice_number,
                "total_amount": total_amount,
                "total_paid": round(total_paid, 2),
                "balance_due": round(max(total_amount - total_paid, 0), 2),
                "payment_count": len(payments),
                "payments": "\n".join(lines) if lines else "No payments recorded yet.",
                "items": items,
                "last_payment_date": payments[0].payment_date.isoformat() if payments else None,
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

    async def _complete_followup(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        followup_id = self._require_uuid(args, "followup_id")
        outcome_note = str(args.get("outcome_note") or "").strip()

        followup = lead_followup_service.get_followup(
            session=self.session,
            current_user=self.current_user,
            followup_id=followup_id,
        )
        customer_name, lead_title = self._get_followup_display_context(followup.lead_id)

        payload = {
            "followup_id": str(followup_id),
            "lead_id": str(followup.lead_id),
            "lead_title": lead_title or "",
            "customer_name": customer_name or "",
            "scheduled_at": followup.scheduled_at.isoformat(),
            "followup_type": "call",
            "outcome_note": outcome_note,
            "new_status": "completed",
        }
        return {
            "data": {"message": "Follow-up completion prepared for review"},
            "action": {
                "type": "confirm_complete_followup",
                "form_name": "complete_followup",
                "prefilled_data": payload,
            },
        }

    async def _reschedule_followup(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        followup_id = self._require_uuid(args, "followup_id")
        new_date = self._require_str(args, "new_date")
        new_time = str(args.get("new_time") or "").strip()
        reason = str(args.get("reason") or "").strip()

        followup = lead_followup_service.get_followup(
            session=self.session,
            current_user=self.current_user,
            followup_id=followup_id,
        )
        if not new_time:
            new_time = followup.scheduled_at.strftime("%H:%M")

        customer_name, lead_title = self._get_followup_display_context(followup.lead_id)
        payload = {
            "followup_id": str(followup_id),
            "lead_id": str(followup.lead_id),
            "lead_title": lead_title or "",
            "customer_name": customer_name or "",
            "original_date": followup.scheduled_at.isoformat(),
            "new_date": new_date,
            "new_time": new_time,
            "reason": reason,
            "followup_type": "call",
        }
        return {
            "data": {"message": "Follow-up reschedule prepared for review"},
            "action": {
                "type": "confirm_reschedule_followup",
                "form_name": "reschedule_followup",
                "prefilled_data": payload,
            },
        }

    async def _bulk_update_followups(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        action = str(args.get("action") or "complete")
        filter_type = str(args.get("filter_type") or "today")
        filter_value = str(args.get("filter_value") or "").strip()
        reschedule_date = str(args.get("reschedule_to_date") or "").strip()
        reschedule_time = str(args.get("reschedule_to_time") or "").strip()
        note = str(args.get("note") or "").strip()
        today = date.today()

        if action == "reschedule" and not reschedule_date:
            raise ValueError("reschedule_to_date is required when action is reschedule")

        query_args: dict[str, Any] = {"status": "pending", "limit": 200}
        if filter_type == "today":
            query_args["date_value"] = today
        elif filter_type == "overdue":
            query_args["before_date"] = today
        elif filter_type == "date_range":
            parts = [part.strip() for part in filter_value.split(",", 1)]
            if not parts or not parts[0]:
                raise ValueError("date_range filter requires 'YYYY-MM-DD,YYYY-MM-DD'")
            query_args["from_date"] = self._parse_date(parts[0])
            query_args["to_date"] = self._parse_date(parts[1] if len(parts) > 1 and parts[1] else parts[0])
        elif filter_type == "customer":
            query_args["customer_id"] = self._require_uuid({"customer_id": filter_value}, "customer_id")
        elif filter_type == "lead":
            query_args["lead_id"] = self._require_uuid({"lead_id": filter_value}, "lead_id")
        elif filter_type == "ids":
            ids = [UUID(item.strip()) for item in filter_value.split(",") if item.strip()]
            if not ids:
                raise ValueError("ids filter requires one or more follow-up IDs")
            query_args["followup_ids"] = ids
        else:
            raise ValueError("Unsupported filter_type")

        followups = lead_followup_service.list_followups(
            session=self.session,
            current_user=self.current_user,
            **query_args,
        )
        if not followups:
            return {"data": {"message": "No matching pending follow-ups found.", "count": 0}}

        action_labels = {"complete": "Mark completed", "cancel": "Cancel/Close", "reschedule": "Reschedule"}
        filter_labels = {
            "today": "today's",
            "overdue": "overdue",
            "date_range": f"from {filter_value}",
            "customer": "for this customer",
            "lead": "for this lead",
            "ids": f"{len(followups)} selected",
        }

        summaries: list[dict[str, Any]] = []
        for followup in followups:
            customer_name, lead_title = self._get_followup_display_context(followup.lead_id)
            summaries.append(
                {
                    "followup_id": str(followup.id),
                    "lead_title": lead_title or "",
                    "customer_name": customer_name or "",
                    "scheduled_at": followup.scheduled_at.isoformat(),
                    "followup_type": "call",
                }
            )

        payload = {
            "action": action,
            "action_label": action_labels.get(action, action),
            "filter_type": filter_type,
            "filter_label": filter_labels.get(filter_type, filter_type),
            "count": len(followups),
            "followups": summaries,
            "followup_ids": [str(followup.id) for followup in followups],
            "reschedule_to_date": reschedule_date if action == "reschedule" else "",
            "reschedule_to_time": reschedule_time if action == "reschedule" else "",
            "note": note,
        }
        return {
            "data": {"message": f"Bulk update prepared: {len(followups)} follow-ups"},
            "action": {
                "type": "confirm_bulk_update_followups",
                "form_name": "bulk_update_followups",
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
                    "sac_code": item.get("sac_code"),
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

    async def _get_invoice_share_link(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        del business_id
        invoice_id = self._require_uuid(args, "invoice_id")
        invoice = invoice_service.get_invoice(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        branded_url = f"https://sellnsettle.com/invoices/{invoice_id}/{invoice.invoice_number}.pdf"
        return {
            "data": {
                "share_link": branded_url,
                "invoice_id": str(invoice_id),
                "invoice_number": invoice.invoice_number,
                "message": f"Share link for {invoice.invoice_number}: {branded_url}",
            }
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

        customer_name,customer_phone, lead_title = self._get_invoice_display_context(invoice.lead_id)
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
                "customer_phone": customer_phone,
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
        customer_name, customer_phone, _ = self._get_invoice_display_context(invoice.lead_id)
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
                customer_name, customer_phone, _ = self._get_invoice_display_context(invoice.lead_id)
                invoice_number = invoice.invoice_number
            except HTTPException:
                customer_name = ""
                customer_phone = ""
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
        invoice_id = str(args.get("invoice_id") or "")
        tone = str(args.get("message_tone") or "polite")

        invoice_link = ""
        if invoice_id and invoice_numbers:
            url = f"https://sellnsettle.com/invoices/{invoice_id}/{invoice_numbers.split(',')[0].strip()}.pdf"
            invoice_link = f"\n\nInvoice: {url}"

        if tone == "firm":
            message = (
                f"Namaste {customer_name} ji,\n\n"
                f"Aapke account mein ₹{outstanding:,.0f} ka outstanding amount hai"
                f"{' (Invoice: ' + invoice_numbers + ')' if invoice_numbers else ''}.\n\n"
                f"Kripya jaldi se jaldi payment karein. Agar koi issue hai toh humse baat karein."
                f"{invoice_link}\n\n"
                f"Dhanyavaad."
            )
        elif tone == "urgent":
            message = (
                f"{customer_name} ji,\n\n"
                f"Aapka ₹{outstanding:,.0f} ka payment kaafi din se pending hai"
                f"{' (' + invoice_numbers + ')' if invoice_numbers else ''}.\n\n"
                f"Kripya aaj hi payment karein. Yeh final reminder hai."
                f"{invoice_link}\n\n"
                f"Dhanyavaad."
            )
        else:
            message = (
                f"Namaste {customer_name} ji,\n\n"
                f"Yeh ek friendly reminder hai ki aapka ₹{outstanding:,.0f} ka payment pending hai"
                f"{' (' + invoice_numbers + ')' if invoice_numbers else ''}.\n\n"
                f"Agar payment ho chuki hai toh please ignore karein."
                f"{invoice_link}\n\n"
                f"Dhanyavaad!"
            )
        payload = {
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "outstanding_amount": outstanding,
            "invoice_numbers": invoice_numbers,
            "invoice_link": invoice_link.strip(),
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
            "query_invoices",
            "list_customer_payments",
            "create_lead",
            "prepare_invoice",
            "get_stale_followups",
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
            "query_invoices",
            "prepare_invoice",
            "add_lead_note",
            "get_stale_followups",
        }:
            args.setdefault("lead_id", str(context_id))

        if context_type == "customer" and tool_name == "bulk_update_followups":
            if args.get("filter_type") == "customer" and not args.get("filter_value"):
                args.setdefault("filter_value", str(context_id))

        if context_type == "lead" and tool_name == "bulk_update_followups":
            if args.get("filter_type") == "lead" and not args.get("filter_value"):
                args.setdefault("filter_value", str(context_id))

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

    def _optional_uuid(self, value: Any) -> UUID | None:
        if value is None or value == "":
            return None
        return UUID(str(value))

    def _parse_date(self, value: str) -> datetime.date:
        return datetime.strptime(value, "%Y-%m-%d").date()

    _STATUS_ALIASES: dict[str, str] = {
        "partially_paid": InvoiceStatus.PARTIAL.value,
        "fully_paid": InvoiceStatus.PAID.value,
    }

    def _optional_invoice_status(self, value: Any) -> InvoiceStatus | None:
        if value is None:
            return None
        raw = str(value).lower().strip()
        raw = self._STATUS_ALIASES.get(raw, raw)
        try:
            return InvoiceStatus(raw)
        except ValueError:
            return None

    def _get_invoice_display_context(self, lead_id: UUID | None) -> tuple[str | None, str | None]:
        if lead_id is None:
            return None, None

        try:
            lead = lead_service.get_lead(self.session, self.current_user, lead_id)
        except HTTPException:
            return None, None

        customer_name = None
        customer_phone = None
        if lead.customer_id is not None:
            try:
                customer = customer_service.get_customer(
                    self.session,
                    self.current_user,
                    lead.customer_id,
                )
                customer_name = customer.name
                customer_phone = customer.phone
            except HTTPException:
                customer_name = None
                customer_phone = None
        return customer_name, customer_phone, lead.title

    def _get_followup_display_context(self, lead_id: UUID | None) -> tuple[str | None, str | None]:
        customer_name, customer_phone, lead_title = self._get_invoice_display_context(lead_id)
        return customer_name, lead_title

    def _optional_payment_method(self, value: Any) -> PaymentMethod:
        if value is None or value == "":
            return PaymentMethod.UPI
        return PaymentMethod(str(value))

    def _optional_decimal(self, value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(str(value))

    def _resolve_query_period(
        self,
        *,
        period: str | None,
        from_value: Any,
        to_value: Any,
    ) -> tuple[date | None, date | None]:
        today = date.today()
        if not period:
            return (
                self._parse_date(str(from_value)) if from_value else None,
                self._parse_date(str(to_value)) if to_value else None,
            )

        if period == "today":
            return today, today
        if period == "this_week":
            return today - timedelta(days=today.weekday()), today
        if period == "this_month":
            return today.replace(day=1), today
        if period == "last_month":
            first_of_this_month = today.replace(day=1)
            last_of_last_month = first_of_this_month - timedelta(days=1)
            return last_of_last_month.replace(day=1), last_of_last_month
        if period == "last_2_months":
            first_of_this_month = today.replace(day=1)
            last_of_previous_month = first_of_this_month - timedelta(days=1)
            first_of_previous_month = last_of_previous_month.replace(day=1)
            last_of_two_months_ago = first_of_previous_month - timedelta(days=1)
            return last_of_two_months_ago.replace(day=1), today
        if period == "last_quarter":
            current_quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            current_quarter_start = today.replace(month=current_quarter_start_month, day=1)
            previous_quarter_end = current_quarter_start - timedelta(days=1)
            previous_quarter_start_month = ((previous_quarter_end.month - 1) // 3) * 3 + 1
            previous_quarter_start = previous_quarter_end.replace(month=previous_quarter_start_month, day=1)
            return previous_quarter_start, previous_quarter_end
        if period == "this_year":
            return today.replace(month=1, day=1), today
        if period == "last_30_days":
            return today - timedelta(days=30), today
        if period == "last_90_days":
            return today - timedelta(days=90), today
        if period == "custom":
            return (
                self._parse_date(str(from_value)) if from_value else None,
                self._parse_date(str(to_value)) if to_value else None,
            )
        return None, None

    def _resolve_analytics_period(self, period: str) -> tuple[date, date]:
        today = date.today()
        if period == "this_month":
            return today.replace(day=1), today
        if period == "last_month":
            first_of_this_month = today.replace(day=1)
            last_of_last_month = first_of_this_month - timedelta(days=1)
            return last_of_last_month.replace(day=1), last_of_last_month
        if period == "this_quarter":
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            return today.replace(month=quarter_start_month, day=1), today
        if period == "last_quarter":
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            current_quarter_start = today.replace(month=quarter_start_month, day=1)
            previous_quarter_end = current_quarter_start - timedelta(days=1)
            previous_quarter_start_month = ((previous_quarter_end.month - 1) // 3) * 3 + 1
            return previous_quarter_end.replace(month=previous_quarter_start_month, day=1), previous_quarter_end
        if period == "last_3_months":
            return today - timedelta(days=90), today
        if period == "last_6_months":
            return today - timedelta(days=180), today
        if period == "this_year":
            return today.replace(month=1, day=1), today
        return today.replace(day=1), today

    def _invoice_matches_search(self, invoice_id: UUID, invoice: Any, search: str) -> bool:
        haystacks = [
            str(getattr(invoice, "invoice_number", "") or "").lower(),
            str(getattr(invoice, "customer_name", "") or "").lower(),
            str(getattr(invoice, "lead_title", "") or "").lower(),
        ]
        if any(search in haystack for haystack in haystacks):
            return True

        invoice_items = invoice_service.list_invoice_items(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )
        for item in invoice_items:
            if search in (item.name or "").lower() or search in (item.description or "").lower():
                return True
        return False

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


    async def _set_onboarding_persona(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        from app.services import onboarding_service

        persona = str(args.get("persona", "other"))
        label = args.get("label")

        business = onboarding_service.set_persona(
            session=self.session,
            business_id=business_id,
            persona=persona,
            label=label,
        )
        stages = onboarding_service.get_pipeline_preview(persona)
        stage_names = [s["name"] for s in stages]

        return {
            "data": {
                "business_type": business.business_type,
                "pipeline_stages": stage_names,
                "message": f"Pipeline set up with stages: {' → '.join(stage_names)}",
            }
        }

    async def _add_onboarding_catalog_item(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:
        from app.services import onboarding_service

        name = str(args.get("name", "")).strip()
        price = float(args.get("price", 0))

        if not name:
            return {"data": {"error": "Item name is required"}}

        item = onboarding_service.add_first_catalog_item(
            session=self.session,
            business_id=business_id,
            name=name,
            price=price,
        )
        return {
            "data": {
                "item_id": str(item.id),
                "name": item.name,
                "price": float(item.default_rate),
                "message": f"Added '{item.name}' at ₹{float(item.default_rate):,.0f} to your catalog.",
            }
        }

    async def _complete_onboarding(self, business_id: UUID, args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        from app.services import onboarding_service

        business = onboarding_service.complete_onboarding(
            session=self.session,
            business_id=business_id,
            method="chat",
        )
        return {
            "data": {
                "onboarding_status": business.onboarding_status,
                "message": "Onboarding completed successfully!",
            }
        }


def build_tool_user(business_id: UUID) -> User:
    return User(
        business_id=business_id,
        name="AI Assistant",
        email="ai-assistant@example.com",
        role=UserRole.OWNER,
    )
