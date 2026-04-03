from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlmodel import Session

from app.models.enums import InvoiceStatus
from app.models.user import User
from app.services import customer_service, dashboard_service, invoice_service, lead_followup_service
from app.services import lead_service, pipeline_service


class SuggestionEngine:
    """
    Generates contextual next-action suggestions using pure business rules.
    """

    def __init__(self, session: Session, current_user: User) -> None:
        self.session = session
        self.current_user = current_user

    async def get_suggestions(
        self,
        context_type: str,
        context_id: UUID | None,
        business_id: UUID,
        last_action: str | None = None,
    ) -> list[str]:
        try:
            if business_id != self.current_user.business_id:
                return []

            suggestions: list[str] = []
            if last_action:
                suggestions = self._suggestions_after_action(
                    last_action=last_action,
                    context_type=context_type,
                    context_id=context_id,
                )
                if suggestions:
                    return suggestions[:3]

            if context_type == "dashboard":
                return (await self._suggestions_for_dashboard(business_id))[:3]
            if context_type == "customer" and context_id is not None:
                return (await self._suggestions_for_customer(context_id, business_id))[:3]
            if context_type == "lead" and context_id is not None:
                return (await self._suggestions_for_lead(context_id, business_id))[:3]
            return (await self._suggestions_for_global(business_id))[:3]
        except Exception:
            return []

    def _suggestions_after_action(
        self,
        last_action: str,
        context_type: str,
        context_id: UUID | None,
    ) -> list[str]:
        mapping = {
            "create_lead": ["Schedule a follow-up", "Add a note to this lead"],
            "update_lead_stage": ["Schedule a follow-up", "Create a quote"],
            "schedule_followup": ["Add a note", "Show follow-up history"],
            "get_todays_followups": ["Show overdue follow-ups", "Create a new lead"],
            "get_customer_outstanding": ["Show unpaid invoices", "Record a payment"],
            "list_customer_invoices": ["Show outstanding amount", "Create a new invoice"],
            "search_customer": ["Show their outstanding", "Show their invoices"],
            "search_lead": ["Show lead details", "Show follow-up history"],
            "get_catalog_items": ["Create a new lead", "Prepare an invoice"],
            "create_invoice": ["Generate PDF", "Share on WhatsApp"],
            "prepare_invoice": ["Review invoice", "Confirm invoice"],
            "generate_invoice_pdf": ["Share on WhatsApp", "Create another invoice"],
            "add_lead_note": ["Review note", "Confirm note"],
        }

        suggestions = list(mapping.get(last_action, []))
        if last_action == "update_lead_stage" and context_type == "lead" and context_id is not None:
            suggestions = ["Schedule a follow-up", "Move to next stage", "Create a quote"]
        return suggestions[:3]

    async def _suggestions_for_dashboard(self, business_id: UUID) -> list[str]:
        summary = dashboard_service.get_dashboard_summary(
            session=self.session,
            current_user=self.current_user,
        )
        suggestions: list[str] = []
        if summary.get("todays_followups_count", 0) > 0:
            suggestions.append("Show today's follow-ups")
        if summary.get("overdue_followups_count", 0) > 0:
            suggestions.append("Show overdue follow-ups")
        suggestions.append("Create a new lead")
        if not suggestions:
            suggestions = ["Show today's follow-ups", "Create a new lead", "Search for a customer"]
        if "Search for a customer" not in suggestions and len(suggestions) < 3:
            suggestions.append("Search for a customer")
        return self._dedupe(suggestions)

    async def _suggestions_for_customer(self, customer_id: UUID, business_id: UUID) -> list[str]:
        del business_id
        outstanding = customer_service.get_customer_outstanding(
            session=self.session,
            business_id=self.current_user.business_id,
            customer_id=customer_id,
        )
        invoices, _, _ = invoice_service.list_customer_invoices(
            session=self.session,
            current_user=self.current_user,
            customer_id=customer_id,
            limit=20,
            offset=0,
        )
        unpaid_count = len(
            [invoice for invoice in invoices if invoice.status not in {InvoiceStatus.PAID, InvoiceStatus.DRAFT}]
        )
        leads = lead_service.list_leads(
            session=self.session,
            current_user=self.current_user,
            customer_id=customer_id,
        )

        suggestions: list[str] = []
        if outstanding.get("outstanding", 0) > 0:
            suggestions.append("Show unpaid invoices")
        if outstanding.get("outstanding", 0) > 0 and unpaid_count > 0:
            suggestions.append("Record a payment")
        if leads:
            latest_lead = leads[0]
            if not self._is_terminal_lead_stage(latest_lead.stage_name):
                suggestions.append("Show latest lead details")
        suggestions.append("Create a new enquiry")

        if not suggestions:
            suggestions = ["Show invoices", "Create a new enquiry", "Show outstanding amount"]
        if len(suggestions) < 3:
            for fallback in ["Show invoices", "Show outstanding amount"]:
                if fallback not in suggestions:
                    suggestions.append(fallback)
                if len(suggestions) >= 3:
                    break
        return self._dedupe(suggestions)

    async def _suggestions_for_lead(self, lead_id: UUID, business_id: UUID) -> list[str]:
        del business_id
        lead = lead_service.get_lead(
            session=self.session,
            current_user=self.current_user,
            lead_id=lead_id,
        )
        stages = pipeline_service.get_stages_for_business(
            session=self.session,
            current_user=self.current_user,
        )
        followups = lead_followup_service.list_followups(
            session=self.session,
            current_user=self.current_user,
            lead_id=lead_id,
        )
        has_upcoming_followup = any(
            followup.status == "pending" and followup.scheduled_at >= datetime.now(timezone.utc)
            for followup in followups
        )

        stage_name = self._stage_name_for_id(stages, lead.stage_id)
        bucket = self._stage_bucket(stages, lead.stage_id, stage_name)
        suggestions: list[str] = []

        if not has_upcoming_followup:
            suggestions.append("Schedule a follow-up")

        if bucket == "early":
            suggestions.extend(["Move to next stage", "Add a note"])
        elif bucket == "middle":
            suggestions.extend(["Move to next stage", "Show follow-up history"])
        elif bucket == "late":
            suggestions.extend(["Create an invoice", "Mark as won"])
            if has_upcoming_followup:
                suggestions.append("Show follow-up history")
        elif bucket == "won":
            suggestions.extend(["Create an invoice", "Show customer outstanding"])
        elif bucket == "lost":
            suggestions.extend(["Add a note", "Move back to an active stage"])

        if not suggestions:
            suggestions = ["Schedule a follow-up", "Add a note", "Move to next stage"]
        return self._dedupe(suggestions)

    async def _suggestions_for_global(self, business_id: UUID) -> list[str]:
        return await self._suggestions_for_dashboard(business_id)

    def _stage_name_for_id(self, stages: list, stage_id: UUID) -> str | None:
        stage = next((item for item in stages if item.id == stage_id), None)
        return stage.name if stage is not None else None

    def _stage_bucket(self, stages: list, stage_id: UUID, stage_name: str | None) -> str:
        name = (stage_name or "").strip().lower()
        if name in {"won", "closed won"}:
            return "won"
        if name in {"lost", "closed lost"}:
            return "lost"

        ordered = sorted(stages, key=lambda item: item.position)
        total = len(ordered)
        if total == 0:
            return "middle"

        index = next((idx for idx, item in enumerate(ordered) if item.id == stage_id), 0)
        if total <= 2:
            return "early" if index == 0 else "late"

        first_cut = max(1, total // 3)
        last_cut = max(first_cut + 1, total - first_cut)
        if index < first_cut:
            return "early"
        if index >= last_cut:
            return "late"
        return "middle"

    def _is_terminal_lead_stage(self, stage_name: str | None) -> bool:
        return (stage_name or "").strip().lower() in {"won", "lost", "closed won", "closed lost"}

    def _dedupe(self, suggestions: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for suggestion in suggestions:
            if suggestion and suggestion not in seen:
                seen.add(suggestion)
                result.append(suggestion)
            if len(result) >= 3:
                break
        return result
