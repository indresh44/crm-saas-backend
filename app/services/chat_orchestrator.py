from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from app.models.chat import ChatThread
from app.models.customer import CustomerCreateRequest
from app.models.lead import LeadCreate
from app.models.lead_followup import LeadFollowupCreate, LeadFollowupUpdate
from app.models.user import User
from app.repositories import chat_message_repo, chat_thread_repo
from app.services import customer_service, lead_followup_service, lead_service, pipeline_service
from app.services import invoice_service, lead_activity_service, payment_service
from app.services.chat.mention_parser import resolve_mentions
from app.services.context_assembler import ContextAssembler
from app.models.chat_metrics import ChatMessageMetrics, ToolCallMetric
from app.services.chat_timer import ChatTimer
from app.services.llm_service import LLMResponse, LLMService
from app.services.suggestion_engine import SuggestionEngine
from app.services.tool_definitions import get_tools_for_context, select_tools_for_message
from app.services.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)


class ChatAction(BaseModel):
    type: str
    form_name: str | None = None
    prefilled_data: dict[str, Any] | None = None


class ChatMessageResponse(BaseModel):
    thread_id: int
    reply: str
    action: ChatAction | None = None
    suggestions: list[str] = []
    tokens_used: int = 0
    pdf: dict[str, Any] | None = None
    metrics: ChatMessageMetrics | None = None


class ChatOrchestrator:
    """
    Coordinates the full chat message lifecycle.
    """

    def __init__(
        self,
        session: Session,
        current_user: User,
        context_assembler: ContextAssembler | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        self.session = session
        self.current_user = current_user
        self.context_assembler = context_assembler or ContextAssembler()
        self.llm_service = llm_service or LLMService()

    async def handle_message(
        self,
        user_message: str,
        context_type: str,
        context_id: UUID | None,
        thread_id: int | None = None,
    ) -> ChatMessageResponse:
        timer = ChatTimer()

        with timer.track("mention_parsing", "other"):
            parsed = resolve_mentions(
                session=self.session,
                current_user=self.current_user,
                message=user_message,
            )

        with timer.track("thread_resolution", "db"):
            thread = self._resolve_thread(
                business_id=self.current_user.business_id,
                context_type=context_type,
                context_id=context_id,
                thread_id=thread_id,
            )

        with timer.track("save_user_msg", "db"):
            chat_message_repo.create(
                session=self.session,
                thread_id=thread.id,
                role="user",
                content=user_message,
            )

        async with timer.track_async("context_assembly", "context"):
            assembled = await self.context_assembler.assemble(
                business_id=self.current_user.business_id,
                thread=thread,
                user_message=user_message,
            )
        if parsed.mention_context:
            assembled.system_prompt = f"{assembled.system_prompt}\n\n{parsed.mention_context}"

        if thread.context_type == "onboarding":
            selected_tools = get_tools_for_context("onboarding")
        else:
            prior_user_messages = [
                m["content"]
                for m in assembled.messages
                if m.get("role") == "user" and isinstance(m.get("content"), str)
            ]
            selected_tools = select_tools_for_message(
                parsed.clean_message,
                recent_user_messages=prior_user_messages,
            )

        assembled.messages.append({"role": "user", "content": parsed.clean_message})

        tool_executor = ToolExecutor(session=self.session, current_user=self.current_user)
        suggestion_engine = SuggestionEngine(session=self.session, current_user=self.current_user)
        pending_action: dict[str, Any] | None = None
        tool_traces: list[dict[str, Any]] = []

        async def _tool_callback(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
            nonlocal pending_action
            result = await tool_executor.execute(
                tool_name=tool_name,
                tool_args=tool_args,
                business_id=self.current_user.business_id,
                context_type=context_type,
                context_id=context_id,
            )
            tool_traces.append(
                {
                    "tool_name": tool_name,
                    "tool_input": tool_args,
                    "tool_output": result.model_dump(),
                }
            )
            if not result.success:
                return {
                    "error": True,
                    "message": result.error or "Tool execution failed",
                }
            if result.is_write and result.action:
                pending_action = result.action
            return result.data

        llm_response = await self.llm_service.chat_with_tool_loop(
            system_prompt=assembled.system_prompt,
            messages=assembled.messages,
            tools=selected_tools,
            tool_executor=_tool_callback,
            timer=timer,
        )

        reply = llm_response.content or (
            "I have prepared this action for your confirmation." if pending_action else ""
        )
        tokens_used = llm_response.input_tokens + llm_response.output_tokens

        async def _save_assistant_msg() -> None:
            with timer.track("save_assistant_msg", "db"):
                chat_message_repo.create(
                    session=self.session,
                    thread_id=thread.id,
                    role="assistant",
                    content=reply,
                    tool_name=self._tool_name_for_storage(tool_traces),
                    tool_input={"tool_calls": [trace["tool_input"] for trace in tool_traces]} if tool_traces else None,
                    tool_output={"tool_results": [trace["tool_output"] for trace in tool_traces]} if tool_traces else None,
                    tokens_used=tokens_used,
                )

        async def _get_suggestions() -> list[str]:
            async with timer.track_async("suggestions", "other"):
                last_action = tool_traces[-1]["tool_name"] if tool_traces else None
                return await suggestion_engine.get_suggestions(
                    context_type=context_type,
                    context_id=context_id,
                    business_id=self.current_user.business_id,
                    last_action=last_action,
                )

        _, suggestions = await asyncio.gather(_save_assistant_msg(), _get_suggestions())
        pdf_payload = self._extract_pdf_payload(tool_traces)

        metrics = None
        try:
            metrics = self._build_metrics(timer=timer, llm_response=llm_response, tool_traces=tool_traces)
        except Exception as exc:
            print(f"METRICS BUILD ERROR: {exc!r}")
            logger.warning("Failed to build chat metrics: %s", exc)

        return ChatMessageResponse(
            thread_id=thread.id,
            reply=reply,
            action=ChatAction(**pending_action) if pending_action else None,
            suggestions=suggestions,
            tokens_used=tokens_used,
            pdf=pdf_payload,
            metrics=metrics,
        )

    async def confirm_action(
        self,
        thread_id: int,
        action_type: str,
        confirmed_data: dict[str, Any],
    ) -> ChatMessageResponse:
        thread = self._get_thread_or_403(thread_id)
        suggestion_engine = SuggestionEngine(session=self.session, current_user=self.current_user)

        pdf_payload = None

        if action_type == "confirm_create_lead":
            result = self._confirm_create_lead(confirmed_data)
            reply = f"✓ Lead created successfully. [View lead details](/leads/{result['lead_id']})"
        elif action_type == "confirm_update_lead_stage":
            result = self._confirm_update_lead_stage(confirmed_data)
            reply = f"✓ Lead moved to {result['stage_name']}."
        elif action_type == "confirm_schedule_followup":
            result = self._confirm_schedule_followup(confirmed_data)
            reply = f"✓ Follow-up scheduled for {result['scheduled_date']}."
        elif action_type == "confirm_complete_followup":
            result = self._confirm_complete_followup(confirmed_data)
            reply = f"✓ Follow-up marked as completed.{' Note: ' + result.get('note', '') if result.get('note') else ''}"
        elif action_type == "confirm_reschedule_followup":
            result = self._confirm_reschedule_followup(confirmed_data)
            reply = f"✓ Follow-up rescheduled to {result['new_date']}{' ' + result.get('new_time', '') if result.get('new_time') else ''}."
        elif action_type == "confirm_bulk_update_followups":
            result = self._confirm_bulk_update_followups(confirmed_data)
            reply = f"✓ {result['count']} follow-ups {result['action_label']}."
        elif action_type == "confirm_create_invoice":
            result = self._confirm_create_invoice(confirmed_data)
            reply = f"✓ Invoice {result['invoice_number']} created for ₹{result['total_amount']:,.2f}"
            pdf_payload = {
                "invoice_id": result["invoice_id"],
                "invoice_number": result["invoice_number"],
            }
        elif action_type == "confirm_update_invoice":
            result = self._confirm_update_invoice(confirmed_data)
            reply = f"✓ {result['summary']}"
        elif action_type == "confirm_add_lead_note":
            result = self._confirm_add_lead_note(confirmed_data)
            reply = "✓ Note added to lead."
        elif action_type == "confirm_record_payment":
            result = self._confirm_record_payment(confirmed_data)
            reply = (
                f"✓ Payment of ₹{result['amount']:,.0f} recorded for {result['invoice_number']}. "
                f"Balance due: ₹{result['balance_remaining']:,.0f}"
            )
        elif action_type == "confirm_send_payment_reminder":
            result = confirmed_data
            reply = (
                f"✓ Payment reminder ready for {confirmed_data.get('customer_name', '')}. "
                f"Click the WhatsApp button below to send it."
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported action type",
            )

        chat_message_repo.create(
            session=self.session,
            thread_id=thread.id,
            role="assistant",
            content=reply,
            tool_name=action_type,
            tool_output=result,
            tokens_used=0,
        )
        suggestions = await suggestion_engine.get_suggestions(
            context_type=thread.context_type,
            context_id=thread.context_id,
            business_id=self.current_user.business_id,
            last_action=action_type.removeprefix("confirm_"),
        )

        return ChatMessageResponse(
            thread_id=thread.id,
            reply=reply,
            action=None,
            suggestions=suggestions,
            tokens_used=0,
            pdf=pdf_payload,
        )

    def _resolve_thread(
        self,
        business_id: UUID,
        context_type: str,
        context_id: UUID | None,
        thread_id: int | None,
    ) -> ChatThread:
        if thread_id is not None:
            return self._get_thread_or_403(thread_id)
        return chat_thread_repo.get_or_create(
            session=self.session,
            business_id=business_id,
            context_type=context_type,
            context_id=context_id,
        )

    def _get_thread_or_403(self, thread_id: int) -> ChatThread:
        thread = chat_thread_repo.get_by_id(self.session, thread_id)
        if thread is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat thread not found",
            )
        if thread.business_id != self.current_user.business_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Chat thread does not belong to this business",
            )
        return thread

    def _confirm_create_lead(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        customer = None
        customer_id = confirmed_data.get("customer_id")
        if customer_id:
            customer = customer_service.get_customer(
                session=self.session,
                current_user=self.current_user,
                customer_id=UUID(str(customer_id)),
            )
        else:
            phone = str(confirmed_data.get("phone", ""))
            customer = customer_service.get_customer_by_phone(
                session=self.session,
                current_user=self.current_user,
                phone=phone,
            )
            if customer is None:
                customer = customer_service.create_customer(
                    session=self.session,
                    current_user=self.current_user,
                    data=CustomerCreateRequest(
                        name=str(confirmed_data.get("name", "")).strip(),
                        phone=phone,
                    ),
                )

        stage_id = confirmed_data.get("stage_id") or confirmed_data.get("default_stage_id")
        if not stage_id:
            stages = pipeline_service.get_stages_for_business(
                session=self.session,
                current_user=self.current_user,
            )
            if not stages:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No pipeline stages available for this business",
                )
            stage_id = stages[0].id

        raw_source = str(confirmed_data.get("source") or "").strip().lower()
        valid_sources = {"walk_in", "whatsapp", "referral", "instagram", "justdial", "website", "other"}
        source = raw_source if raw_source in valid_sources else ("other" if raw_source else None)

        lead = lead_service.create_lead(
            session=self.session,
            current_user=self.current_user,
            data=LeadCreate(
                customer_id=customer.id if customer is not None else None,
                stage_id=UUID(str(stage_id)),
                title=str(confirmed_data.get("requirement") or f"Enquiry from {confirmed_data.get('name', 'customer')}"),
                source=source,
                estimated_value=self._to_decimal(confirmed_data.get("estimated_value")),
                notes=confirmed_data.get("notes"),
            ),
        )
        return {
            "lead_id": str(lead.id),
            "customer_id": str(customer.id) if customer is not None else None,
            "title": lead.title,
        }

    def _confirm_update_lead_stage(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        lead = lead_service.move_lead_stage(
            session=self.session,
            current_user=self.current_user,
            lead_id=UUID(str(confirmed_data["lead_id"])),
            new_stage_id=UUID(str(confirmed_data["target_stage_id"])),
        )
        stages = pipeline_service.get_stages_for_business(
            session=self.session,
            current_user=self.current_user,
        )
        stage_name = next((stage.name for stage in stages if stage.id == lead.stage_id), "Updated")
        return {
            "lead_id": str(lead.id),
            "stage_id": str(lead.stage_id),
            "stage_name": stage_name,
        }

    def _confirm_schedule_followup(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        scheduled_at_raw = confirmed_data.get("scheduled_at")
        if scheduled_at_raw:
            scheduled_at = datetime.fromisoformat(str(scheduled_at_raw))
        else:
            scheduled_date = datetime.fromisoformat(f"{confirmed_data['scheduled_date']}T09:00:00+00:00")
            scheduled_at = scheduled_date

        followup = lead_followup_service.create_followup(
            session=self.session,
            current_user=self.current_user,
            data=LeadFollowupCreate(
                lead_id=UUID(str(confirmed_data["lead_id"])),
                scheduled_at=scheduled_at,
                note=confirmed_data.get("note"),
            ),
        )
        return {
            "followup_id": str(followup.id),
            "lead_id": str(followup.lead_id),
            "scheduled_date": scheduled_at.date().isoformat(),
            "scheduled_at": followup.scheduled_at.isoformat(),
            "note": followup.note,
        }

    def _confirm_complete_followup(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        followup_id = UUID(str(confirmed_data["followup_id"]))
        outcome_note = str(confirmed_data.get("outcome_note") or "").strip()

        followup = lead_followup_service.update_followup(
            session=self.session,
            current_user=self.current_user,
            followup_id=followup_id,
            data=LeadFollowupUpdate(
                status="done",
                note=outcome_note or None,
                completed_at=datetime.now(timezone.utc),
            ),
        )
        return {
            "followup_id": str(followup.id),
            "note": outcome_note,
        }

    def _confirm_reschedule_followup(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        followup_id = UUID(str(confirmed_data["followup_id"]))
        new_date = str(confirmed_data.get("new_date") or "").strip()
        new_time = str(confirmed_data.get("new_time") or "").strip()
        reason = str(confirmed_data.get("reason") or "").strip()

        if new_time:
            new_scheduled_at = datetime.fromisoformat(f"{new_date}T{new_time}:00+00:00")
        else:
            new_scheduled_at = datetime.fromisoformat(f"{new_date}T09:00:00+00:00")

        followup = lead_followup_service.update_followup(
            session=self.session,
            current_user=self.current_user,
            followup_id=followup_id,
            data=LeadFollowupUpdate(
                scheduled_at=new_scheduled_at,
                note=reason or None,
                status="pending",
                completed_at=None,
            ),
        )
        return {
            "followup_id": str(followup.id),
            "new_date": new_scheduled_at.date().isoformat(),
            "new_time": new_scheduled_at.strftime("%H:%M"),
        }

    def _confirm_bulk_update_followups(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        action = str(confirmed_data.get("action") or "complete")
        followup_ids = confirmed_data.get("followup_ids", [])
        note = str(confirmed_data.get("note") or "").strip()
        reschedule_date = str(confirmed_data.get("reschedule_to_date") or "").strip()
        reschedule_time = str(confirmed_data.get("reschedule_to_time") or "").strip()

        count = 0
        for fid_str in followup_ids:
            try:
                followup_id = UUID(str(fid_str))
                if action == "complete":
                    lead_followup_service.update_followup(
                        session=self.session,
                        current_user=self.current_user,
                        followup_id=followup_id,
                        data=LeadFollowupUpdate(
                            status="done",
                            note=note or "Bulk completed",
                            completed_at=datetime.now(timezone.utc),
                        ),
                    )
                elif action == "cancel":
                    lead_followup_service.update_followup(
                        session=self.session,
                        current_user=self.current_user,
                        followup_id=followup_id,
                        data=LeadFollowupUpdate(
                            status="cancelled",
                            note=note or "Bulk cancelled",
                            completed_at=None,
                        ),
                    )
                elif action == "reschedule":
                    if reschedule_time:
                        new_dt = datetime.fromisoformat(f"{reschedule_date}T{reschedule_time}:00+00:00")
                    else:
                        new_dt = datetime.fromisoformat(f"{reschedule_date}T09:00:00+00:00")
                    lead_followup_service.update_followup(
                        session=self.session,
                        current_user=self.current_user,
                        followup_id=followup_id,
                        data=LeadFollowupUpdate(
                            scheduled_at=new_dt,
                            note=note or "Bulk rescheduled",
                            status="pending",
                            completed_at=None,
                        ),
                    )
                count += 1
            except Exception as exc:
                logger.error("Bulk followup update failed for %s: %s", fid_str, exc)
                continue

        action_labels = {"complete": "marked completed", "cancel": "cancelled", "rescheduled": "rescheduled", "reschedule": "rescheduled"}
        return {"count": count, "action_label": action_labels.get(action, action)}

    def _confirm_create_invoice(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        from app.models.enums import InvoiceStatus
        from app.models.invoice_item import InvoiceItemCreate
        from app.services.invoice_service import InvoiceCreateWithItems, InvoiceData

        customer_id = confirmed_data.get("customer_id")
        lead_id = confirmed_data.get("lead_id")

        if not lead_id and customer_id:
            leads = lead_service.list_leads(
                session=self.session,
                current_user=self.current_user,
                customer_id=UUID(str(customer_id)),
            )
            if leads:
                lead_id = str(leads[0].id)

        if not lead_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invoice creation requires an associated lead for this customer",
            )

        items = [
            InvoiceItemCreate(
                catalog_item_id=UUID(str(item["catalog_item_id"])) if item.get("catalog_item_id") else None,
                name=str(item.get("name") or "").strip(),
                description=str(item.get("description") or item.get("name") or "").strip(),
                unit=str(item.get("unit") or "piece"),
                quantity=self._to_decimal(item.get("quantity")) or Decimal("1"),
                unit_price=self._to_decimal(item.get("rate")) or Decimal("0"),
                gst_percent=self._to_decimal(item.get("gst_percent")) or Decimal("18"),
            )
            for item in confirmed_data.get("items", [])
        ]

        invoice = invoice_service.create_invoice(
            session=self.session,
            current_user=self.current_user,
            data=InvoiceCreateWithItems(
                invoice=InvoiceData(
                    lead_id=UUID(str(lead_id)),
                    status=InvoiceStatus.DRAFT,
                    issued_date=datetime.fromisoformat(str(confirmed_data["issued_date"])).date(),
                    due_date=datetime.fromisoformat(str(confirmed_data["due_date"])).date(),
                ),
                items=items,
            ),
        )
        return {
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "total_amount": float(invoice.total_amount),
        }

    def _confirm_update_invoice(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        from app.models.enums import InvoiceStatus
        from app.models.invoice_item import InvoiceItemCreate
        from app.services.invoice_service import InvoiceUpdateData, InvoiceUpdateWithItems

        invoice_id = UUID(str(confirmed_data["invoice_id"]))
        changes = confirmed_data.get("changes", {})
        proposed_items = confirmed_data.get("proposed_items")
        invoice_number = str(confirmed_data.get("invoice_number") or "")

        invoice = invoice_service.get_invoice(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
        )

        update_data = InvoiceUpdateData()
        summary_parts: list[str] = []

        new_status = changes.get("new_status")
        if new_status:
            update_data.status = InvoiceStatus(str(new_status))
            summary_parts.append(f"status → {new_status}")

        new_due_date = changes.get("new_due_date")
        if new_due_date:
            update_data.due_date = date.fromisoformat(str(new_due_date))
            summary_parts.append(f"due date → {new_due_date}")

        items = None
        if proposed_items is not None:
            items = [
                InvoiceItemCreate(
                    catalog_item_id=UUID(str(item["catalog_item_id"])) if item.get("catalog_item_id") else None,
                    name=str(item.get("name") or "").strip(),
                    description=str(item.get("description") or item.get("name") or "").strip(),
                    unit=str(item.get("unit") or "piece"),
                    quantity=self._to_decimal(item.get("quantity")) or Decimal("1"),
                    unit_price=self._to_decimal(item.get("rate")) or Decimal("0"),
                    gst_percent=self._to_decimal(item.get("gst_percent")) or Decimal("18"),
                )
                for item in proposed_items
            ]
            added = changes.get("added_items", [])
            updated = changes.get("updated_items", [])
            removed = changes.get("removed_items", [])
            if added:
                summary_parts.append(f"added: {', '.join(added)}")
            if updated:
                summary_parts.append(f"updated: {', '.join(str(item) for item in updated)}")
            if removed:
                summary_parts.append(f"removed: {', '.join(removed)}")

        updated_invoice = invoice_service.update_invoice(
            session=self.session,
            current_user=self.current_user,
            invoice_id=invoice_id,
            data=InvoiceUpdateWithItems(invoice=update_data, items=items),
        )

        if proposed_items is not None:
            summary_parts.append(f"new total: ₹{float(updated_invoice.total_amount):,.0f}")

        display_number = invoice_number or invoice.invoice_number
        return {
            "invoice_id": str(updated_invoice.id),
            "invoice_number": display_number,
            "summary": (
                f"Invoice {display_number} updated — {', '.join(summary_parts)}"
                if summary_parts
                else f"Invoice {display_number} updated"
            ),
        }

    def _confirm_add_lead_note(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        from app.models.enums import LeadActivityType
        from app.models.lead import LeadActivityCreate

        lead_id = UUID(str(confirmed_data["lead_id"]))
        note = str(confirmed_data.get("note") or "").strip()
        if not note:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Note content is required",
            )

        lead_activity_service.create_activity(
            session=self.session,
            current_user=self.current_user,
            lead_id=lead_id,
            data=LeadActivityCreate(
                lead_id=lead_id,
                type=LeadActivityType.NOTE,
                description=note,
                created_by=self.current_user.id,
            ),
        )
        return {
            "lead_id": str(lead_id),
            "note": note,
        }

    def _confirm_record_payment(self, confirmed_data: dict[str, Any]) -> dict[str, Any]:
        from app.models.payment import PaymentCreate

        invoice_id = UUID(str(confirmed_data["invoice_id"]))
        amount = self._to_decimal(confirmed_data.get("amount"))
        if amount is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment amount is required",
            )

        payment = payment_service.create_payment(
            session=self.session,
            current_user=self.current_user,
            data=PaymentCreate(
                invoice_id=invoice_id,
                amount=amount,
                payment_method=str(confirmed_data.get("payment_method") or "upi"),
                payment_date=datetime.fromisoformat(str(confirmed_data["payment_date"])).date(),
                reference=str(confirmed_data.get("reference") or "") or None,
            ),
        )

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
        total_paid = sum((payment_item.amount for payment_item in payments), Decimal("0"))
        balance_remaining = max(invoice.total_amount - total_paid, Decimal("0"))

        return {
            "invoice_id": str(invoice_id),
            "invoice_number": str(confirmed_data.get("invoice_number") or invoice.invoice_number),
            "amount": float(amount),
            "balance_remaining": float(balance_remaining),
            "payment_id": str(payment.id),
        }

    def _tool_name_for_storage(self, tool_traces: list[dict[str, Any]]) -> str | None:
        if not tool_traces:
            return None
        if len(tool_traces) == 1:
            return str(tool_traces[0]["tool_name"])
        return "tool_loop"

    def _extract_pdf_payload(self, tool_traces: list[dict[str, Any]]) -> dict[str, Any] | None:
        for trace in reversed(tool_traces):
            output = trace.get("tool_output") or {}
            data = output.get("data") or {}
            pdf_url = data.get("pdf_url")
            if pdf_url:
                return {
                    "url": pdf_url,
                    "invoice_id": data.get("invoice_id"),
                    "invoice_number": data.get("invoice_number"),
                }
            if data.get("share_link"):
                return {
                    "invoice_id": data.get("invoice_id"),
                    "invoice_number": data.get("invoice_number"),
                }
        return None

    def _to_decimal(self, value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(str(value))

    def _build_metrics(
        self,
        timer: ChatTimer,
        llm_response: LLMResponse,
        tool_traces: list[dict[str, Any]],
    ) -> ChatMessageMetrics:
        total_ms = timer.get_total_ms()
        context_ms = timer.get_category_total("context")
        llm_ms = timer.get_category_total("llm")
        tool_ms = timer.get_category_total("tool")
        db_ms = timer.get_category_total("db")
        suggestion_ms = sum(
            s.duration_ms for s in timer.get_segments_by_category("other")
            if s.name == "suggestions"
        )
        overhead_ms = total_ms - context_ms - llm_ms - tool_ms - db_ms - suggestion_ms

        tool_segments = timer.get_segments_by_category("tool")
        tool_call_metrics = []
        for i, segment in enumerate(tool_segments):
            tool_name = segment.name.removeprefix("tool:")
            success = True
            if i < len(tool_traces):
                output = tool_traces[i].get("tool_output") or {}
                success = output.get("success", True)
            tool_call_metrics.append(ToolCallMetric(
                tool_name=tool_name,
                duration_ms=round(segment.duration_ms, 2),
                success=success,
            ))

        total_input = llm_response.input_tokens
        total_output = llm_response.output_tokens

        return ChatMessageMetrics(
            total_duration_ms=round(total_ms, 2),
            context_assembly_ms=round(context_ms, 2),
            llm_total_ms=round(llm_ms, 2),
            tool_total_ms=round(tool_ms, 2),
            suggestion_ms=round(suggestion_ms, 2),
            db_total_ms=round(db_ms, 2),
            overhead_ms=round(max(overhead_ms, 0), 2),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_input + total_output,
            estimated_cost_usd=self._estimate_cost(
                model=llm_response.model,
                input_tokens=total_input,
                output_tokens=total_output,
            ),
            llm_rounds=llm_response.total_rounds,
            tools_called=[s.name.removeprefix("tool:") for s in tool_segments],
            model=llm_response.model,
            llm_calls=llm_response.llm_call_metrics or [],
            tool_calls=tool_call_metrics,
        )

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float | None:
        COST_TABLE: dict[str, tuple[float, float]] = {
            "gemini-2.5-flash": (0.15, 0.60),
            "gemini-3-flash": (0.15, 0.60),
            "gemini-3-flash-preview": (0.50, 1.00),
            "gemini-2.0-flash": (0.10, 0.40),
            "gemini-2.5-pro": (1.25, 10.00),
            "gemini-3.1-flash-lite-preview": (0.25, 0.50),
            "claude-sonnet-4": (3.00, 15.00),
            "claude-haiku-4": (0.80, 4.00),
            "gpt-4.1-nano": (0.10, 0.40),
            "gpt-5.4-nano": (0.20, 1.25),
            "gpt-5.4-mini": (0.75, 4.50),
            "gpt-5.4": (2.50, 15.00),
            "gpt-4o-mini": (0.15, 0.60),
            "gpt-4o": (2.50, 10.00),
        }
        model_lower = model.lower()
        for key, (input_price, output_price) in COST_TABLE.items():
            if key in model_lower:
                cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
                return round(cost, 6)
        return None
