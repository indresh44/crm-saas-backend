from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlmodel import Session

from app.config.prompts.system_prompt import SYSTEM_PROMPT
from app.core.database import engine
from app.models.chat import ChatThread
from app.models.enums import InvoiceStatus, UserRole
from app.models.user import User
from app.repositories import chat_message_repo
from app.services import business_service, customer_service, dashboard_service, invoice_service
from app.services import lead_followup_service, lead_service, pipeline_service, quote_service
from app.services.tool_definitions import get_tools_for_context


@dataclass
class AssembledContext:
    system_prompt: str
    messages: list[dict]
    tools: list[dict]
    context_metadata: dict


class ContextAssembler:
    """
    Assembles the LLM context from business, page, pipeline, and chat-history layers.
    """

    async def assemble(
        self,
        business_id: UUID,
        thread: ChatThread,
        user_message: str,
    ) -> AssembledContext:
        business_context, page_context, pipeline_context, messages = await asyncio.gather(
            self._build_business_context(business_id),
            self._build_page_context(thread.context_type, thread.context_id, business_id),
            self._build_pipeline_stages(business_id),
            self._build_messages(thread, user_message=user_message),
        )

        system_prompt = "\n\n".join(
            [
                SYSTEM_PROMPT.format(today_date=date.today().strftime("%d %b %Y")),
                business_context,
                page_context,
                pipeline_context,
            ]
        )

        return AssembledContext(
            system_prompt=system_prompt,
            messages=messages,
            tools=get_tools_for_context(thread.context_type),
            context_metadata={
                "business_id": str(business_id),
                "context_type": thread.context_type,
                "context_id": str(thread.context_id) if thread.context_id is not None else None,
                "thread_id": thread.id,
            },
        )

    async def _build_business_context(self, business_id: UUID) -> str:
        def load() -> str:
            with Session(engine) as session:
                business = business_service.get_business(session, business_id)
                return "\n".join(
                    [
                        "BUSINESS:",
                        f"Name: {business.name}",
                        f"GST: {business.gst_number or 'Not registered'}",
                        "Default tax rate: 18%",
                        f"Invoice prefix: {business.invoice_prefix or 'INV'}",
                    ]
                )

        return await asyncio.to_thread(load)

    async def _build_page_context(
        self,
        context_type: str,
        context_id: UUID | None,
        business_id: UUID,
    ) -> str:
        if context_type == "dashboard":
            return await self._build_dashboard_context(business_id)
        if context_type == "customer" and context_id is not None:
            return await self._build_customer_context(context_id, business_id)
        if context_type == "lead" and context_id is not None:
            return await self._build_lead_context(context_id, business_id)
        return await self._build_global_context(business_id)

    async def _build_dashboard_context(self, business_id: UUID) -> str:
        def load() -> str:
            with Session(engine) as session:
                current_user = self._build_user(business_id)
                summary = dashboard_service.get_dashboard_summary(session, current_user)
                recent_leads = summary.get("recent_leads", [])[:5]
                lines = [
                    "USER IS ON: Dashboard",
                    "",
                    "TODAY'S SNAPSHOT:",
                    f"- Follow-ups today: {summary.get('todays_followups_count', 0)}",
                    f"- Overdue follow-ups: {summary.get('overdue_followups_count', 0)}",
                    f"- Total outstanding: {self._format_amount(summary.get('total_outstanding', 0))}",
                    "",
                    "RECENT LEADS:",
                    self._format_leads_compact(recent_leads),
                ]
                return "\n".join(lines)

        return await asyncio.to_thread(load)

    async def _build_customer_context(self, customer_id: UUID, business_id: UUID) -> str:
        def load() -> str:
            with Session(engine) as session:
                current_user = self._build_user(business_id)
                customer = customer_service.get_customer(session, current_user, customer_id)
                outstanding = customer_service.get_customer_outstanding(session, business_id, customer_id)
                invoices, total, _ = invoice_service.list_customer_invoices(
                    session=session,
                    current_user=current_user,
                    customer_id=customer_id,
                    limit=20,
                    offset=0,
                )
                unpaid_invoices = [
                    invoice
                    for invoice in invoices
                    if invoice.status not in {InvoiceStatus.PAID, InvoiceStatus.DRAFT}
                ][:5]
                leads = lead_service.list_leads(session, current_user, customer_id=customer_id)[:3]

                lines = [
                    f"USER IS ON: Customer page — {customer.name}",
                    f"Phone: {customer.phone}",
                    f"Email: {customer.email or 'N/A'}",
                    f"Outstanding: {self._format_amount(outstanding.get('outstanding', 0))}",
                    "",
                    f"UNPAID INVOICES ({min(len(unpaid_invoices), total)}):",
                    self._format_invoices_compact(unpaid_invoices),
                    "",
                    "RECENT LEADS:",
                    self._format_leads_compact(leads),
                ]
                return "\n".join(lines)

        return await asyncio.to_thread(load)

    async def _build_lead_context(self, lead_id: UUID, business_id: UUID) -> str:
        def load() -> str:
            with Session(engine) as session:
                current_user = self._build_user(business_id)
                lead = lead_service.get_lead(session, current_user, lead_id)
                customer_name = "Unknown customer"
                customer_phone = "N/A"
                if lead.customer_id is not None:
                    customer = customer_service.get_customer(session, current_user, lead.customer_id)
                    customer_name = customer.name
                    customer_phone = customer.phone

                stages = pipeline_service.get_stages_for_business(session, current_user)
                stage_name = next((stage.name for stage in stages if stage.id == lead.stage_id), "Unknown")

                followups = lead_followup_service.list_followups(session, current_user, lead_id)
                followups.sort(key=lambda item: item.scheduled_at, reverse=True)
                recent_followups = followups[:5]

                quotes = quote_service.list_quotes_for_lead(session, current_user, lead_id, limit=3)
                quote_lines = self._format_quotes_compact(quotes)

                lines = [
                    f"USER IS ON: Lead page — {customer_name}'s enquiry",
                    f"Lead ID: #{lead.id}",
                    f"Customer phone: {customer_phone}",
                    f"Stage: {stage_name}",
                    f"Requirement: {lead.title or 'Not specified'}",
                    f"Estimated value: {self._format_amount(lead.estimated_value or 0) if lead.estimated_value is not None else 'N/A'}",
                    f"Source: {lead.source or 'Not specified'}",
                    f"Created: {self._format_date(lead.created_at)}",
                    f"Notes: {lead.notes or 'None'}",
                    "",
                    "FOLLOW-UP HISTORY:",
                    self._format_followups_compact(recent_followups),
                    "",
                    f"QUOTES ({len(quotes)}):",
                    quote_lines,
                ]
                return "\n".join(lines)

        return await asyncio.to_thread(load)

    async def _build_global_context(self, business_id: UUID) -> str:
        del business_id
        return "USER IS ON: Global chat (no specific page)"

    async def _build_pipeline_stages(self, business_id: UUID) -> str:
        def load() -> str:
            with Session(engine) as session:
                current_user = self._build_user(business_id)
                stages = pipeline_service.get_stages_for_business(session, current_user)
                lines = ["PIPELINE STAGES (in order):"]
                if not stages:
                    lines.append("- None")
                else:
                    lines.extend(f"- #{stage.id} {stage.name}" for stage in stages)
                return "\n".join(lines)

        return await asyncio.to_thread(load)

    async def _build_messages(self, thread: ChatThread, user_message: str) -> list[dict]:
        def load() -> list[dict]:
            with Session(engine) as session:
                messages: list[dict] = []
                if thread.summary:
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Previous conversation context: {thread.summary}",
                        }
                    )
                    messages.append(
                        {
                            "role": "assistant",
                            "content": "Understood, I have the context of our previous conversation.",
                        }
                    )

                recent_messages = chat_message_repo.get_recent(session, thread.id, limit=10)
                skipped_current_user = False
                for message in recent_messages:
                    if message.role == "tool_result":
                        continue
                    if (
                        not skipped_current_user
                        and message.role == "user"
                        and message.content == user_message
                    ):
                        skipped_current_user = True
                        continue
                    if message.role in {"user", "assistant"}:
                        messages.append({"role": message.role, "content": message.content})
                return messages

        return await asyncio.to_thread(load)

    def _build_user(self, business_id: UUID) -> User:
        return User(
            business_id=business_id,
            name="SellNSettle AI",
            email="ai@sellnsettle.local",
            role=UserRole.OWNER,
        )

    def _format_amount(self, amount: float | int | Decimal) -> str:
        if isinstance(amount, Decimal):
            amount = float(amount)
        is_negative = float(amount) < 0
        amount_value = abs(float(amount))
        integer_part, _, fraction_part = f"{amount_value:.2f}".partition(".")
        if len(integer_part) > 3:
            last_three = integer_part[-3:]
            remaining = integer_part[:-3]
            groups: list[str] = []
            while len(remaining) > 2:
                groups.insert(0, remaining[-2:])
                remaining = remaining[:-2]
            if remaining:
                groups.insert(0, remaining)
            integer_part = ",".join(groups + [last_three])
        formatted = f"₹{'-' if is_negative else ''}{integer_part}"
        if fraction_part != "00":
            formatted = f"{formatted}.{fraction_part}"
        return formatted

    def _format_date(self, dt) -> str:
        if dt is None:
            return "N/A"
        if isinstance(dt, str):
            return dt
        return dt.strftime("%d %b %Y")

    def _format_leads_compact(self, leads: list) -> str:
        if not leads:
            return "None"

        lines: list[str] = []
        for lead in leads:
            lead_id = getattr(lead, "id", None) or lead.get("id")
            customer_name = getattr(lead, "customer_name", None) if hasattr(lead, "customer_name") else lead.get("customer_name")
            stage_name = getattr(lead, "stage_name", None) if hasattr(lead, "stage_name") else lead.get("stage_name")
            value = getattr(lead, "estimated_value", None) if hasattr(lead, "estimated_value") else lead.get("estimated_value")
            requirement = getattr(lead, "title", None) if hasattr(lead, "title") else lead.get("title")
            lines.append(
                f"- #{lead_id} {customer_name or 'Unknown'} | {stage_name or 'N/A'} | "
                f"{self._format_amount(value) if value is not None else 'N/A'} | {requirement or 'Not specified'}"
            )
        return "\n".join(lines)

    def _format_invoices_compact(self, invoices: list) -> str:
        if not invoices:
            return "None"

        lines = [
            f"- #{invoice.id} {invoice.invoice_number} | {self._format_date(invoice.issued_date)} | "
            f"{self._format_amount(invoice.total_amount)} | {invoice.status.value}"
            for invoice in invoices
        ]
        return "\n".join(lines)

    def _format_followups_compact(self, followups: list) -> str:
        if not followups:
            return "None"

        lines = [
            f"- {self._format_date(followup.scheduled_at)} | {followup.status} | {followup.note or 'No note'}"
            for followup in followups
        ]
        return "\n".join(lines)

    def _format_quotes_compact(self, quotes: list) -> str:
        if not quotes:
            return "None"

        lines = [
            f"- #{quote.id} | {self._format_date(quote.created_at)} | {self._format_amount(quote.total_amount)} | {quote.status.value}"
            for quote in quotes
        ]
        return "\n".join(lines)
