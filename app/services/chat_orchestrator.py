from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from app.config.llm_config import llm_settings
from app.core.database import engine
from app.models.chat import ChatThread
from app.models.customer import CustomerCreateRequest
from app.models.lead import LeadCreate
from app.models.lead_followup import LeadFollowupCreate
from app.models.user import User
from app.repositories import chat_message_repo, chat_thread_repo
from app.services import customer_service, lead_followup_service, lead_service, pipeline_service
from app.services.context_assembler import ContextAssembler
from app.services.llm_service import LLMResponse, LLMService
from app.services.suggestion_engine import SuggestionEngine
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
        thread = self._resolve_thread(
            business_id=self.current_user.business_id,
            context_type=context_type,
            context_id=context_id,
            thread_id=thread_id,
        )

        chat_message_repo.create(
            session=self.session,
            thread_id=thread.id,
            role="user",
            content=user_message,
        )

        assembled = await self.context_assembler.assemble(
            business_id=self.current_user.business_id,
            thread=thread,
            user_message=user_message,
        )
        assembled.messages.append({"role": "user", "content": user_message})

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
            if result.is_write and result.action:
                pending_action = result.action
            return result.data

        llm_response = await self.llm_service.chat_with_tool_loop(
            system_prompt=assembled.system_prompt,
            messages=assembled.messages,
            tools=assembled.tools,
            tool_executor=_tool_callback,
        )

        reply = llm_response.content or (
            "I have prepared this action for your confirmation." if pending_action else ""
        )
        tokens_used = llm_response.input_tokens + llm_response.output_tokens

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

        asyncio.create_task(self._maybe_summarize(thread.id))
        last_action = tool_traces[-1]["tool_name"] if tool_traces else None
        suggestions = await suggestion_engine.get_suggestions(
            context_type=context_type,
            context_id=context_id,
            business_id=self.current_user.business_id,
            last_action=last_action,
        )

        return ChatMessageResponse(
            thread_id=thread.id,
            reply=reply,
            action=ChatAction(**pending_action) if pending_action else None,
            suggestions=suggestions,
            tokens_used=tokens_used,
        )

    async def _maybe_summarize(self, thread_id: int) -> None:
        try:
            with Session(engine) as session:
                message_count = chat_message_repo.count(session, thread_id)
                if message_count <= 10:
                    return

                older_messages = chat_message_repo.get_older_than_recent(
                    session=session,
                    thread_id=thread_id,
                    recent_limit=10,
                )
                if not older_messages:
                    return

                thread = chat_thread_repo.get_by_id(session, thread_id)
                if thread is None:
                    return

                history_text = "\n".join(
                    f"{message.role}: {message.content}"
                    for message in older_messages
                    if message.content
                )
                summary_input = history_text
                if thread.summary:
                    summary_input = f"Existing summary:\n{thread.summary}\n\nNew history:\n{history_text}"

                response = await self.llm_service.chat(
                    system_prompt=(
                        "Summarize this conversation history into a brief paragraph. "
                        "Focus on what was discussed, what actions were taken, what decisions were made, "
                        "and any pending items. Include specific names, amounts, and IDs mentioned. "
                        "Keep it under 150 words."
                    ),
                    messages=[{"role": "user", "content": summary_input}],
                    model=llm_settings.summarization_model,
                )
                if response.content:
                    chat_thread_repo.update_summary(session, thread_id, response.content)
                    chat_message_repo.delete_older_than_recent(session, thread_id, recent_limit=10)
        except Exception as exc:
            logger.error("Chat summarization failed for thread=%s error=%s", thread_id, str(exc), exc_info=True)

    async def confirm_action(
        self,
        thread_id: int,
        action_type: str,
        confirmed_data: dict[str, Any],
    ) -> ChatMessageResponse:
        thread = self._get_thread_or_403(thread_id)
        suggestion_engine = SuggestionEngine(session=self.session, current_user=self.current_user)

        if action_type == "confirm_create_lead":
            result = self._confirm_create_lead(confirmed_data)
            reply = f"✓ Lead created successfully. Lead ID: {result['lead_id']}"
        elif action_type == "confirm_update_lead_stage":
            result = self._confirm_update_lead_stage(confirmed_data)
            reply = f"✓ Lead moved to {result['stage_name']}."
        elif action_type == "confirm_schedule_followup":
            result = self._confirm_schedule_followup(confirmed_data)
            reply = f"✓ Follow-up scheduled for {result['scheduled_date']}."
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

        lead = lead_service.create_lead(
            session=self.session,
            current_user=self.current_user,
            data=LeadCreate(
                customer_id=customer.id if customer is not None else None,
                stage_id=UUID(str(stage_id)),
                title=str(confirmed_data.get("requirement") or f"Enquiry from {confirmed_data.get('name', 'customer')}"),
                source=confirmed_data.get("source"),
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

    def _tool_name_for_storage(self, tool_traces: list[dict[str, Any]]) -> str | None:
        if not tool_traces:
            return None
        if len(tool_traces) == 1:
            return str(tool_traces[0]["tool_name"])
        return "tool_loop"

    def _to_decimal(self, value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(str(value))
