from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlmodel import Session

from app.config.prompts.onboarding_prompt import ONBOARDING_PROMPT
from app.config.prompts.system_prompt import SYSTEM_PROMPT
from app.core.database import engine
from app.core.time_utils import format_local, now_in
from app.models.chat import ChatThread
from app.models.enums import UserRole
from app.models.user import User
from app.repositories import chat_message_repo
from app.services import business_service, pipeline_service
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
        if thread.context_type == "onboarding":
            return await self._assemble_onboarding(business_id, thread, user_message)

        (business_context, preferred_language, timezone), pipeline_context, messages = await asyncio.gather(
            self._build_business_context(business_id),
            self._build_pipeline_stages(business_id),
            self._build_messages(thread, user_message=user_message),
        )

        system_prompt = "\n\n".join(
            [
                SYSTEM_PROMPT.format(
                    today_date=format_local(now_in(timezone), timezone),
                    preferred_language=preferred_language,
                ),
                business_context,
                pipeline_context,
            ]
        )

        return AssembledContext(
            system_prompt=system_prompt,
            messages=messages,
            tools=[],
            context_metadata={
                "business_id": str(business_id),
                "context_type": thread.context_type,
                "context_id": str(thread.context_id) if thread.context_id is not None else None,
                "thread_id": thread.id,
            },
        )

    async def _assemble_onboarding(
        self,
        business_id: UUID,
        thread: ChatThread,
        user_message: str,
    ) -> AssembledContext:
        """Build context specifically for onboarding conversations."""

        def load_business() -> dict:
            with Session(engine) as session:
                business = business_service.get_business(session, business_id)
                msg_count = chat_message_repo.count(session, thread.id)
                return {
                    "business_name": business.name,
                    "business_city": business.city or "",
                    "onboarding_status": business.onboarding_status,
                    "business_type": business.business_type or "not set",
                    "preferred_language": business.preferred_language or "hinglish",
                    "language_chosen": "yes" if msg_count > 0 else "no",
                    "timezone": business.timezone or "Asia/Kolkata",
                }

        biz_data, messages = await asyncio.gather(
            asyncio.to_thread(load_business),
            self._build_messages(thread, user_message=user_message),
        )
        tz = biz_data.pop("timezone")

        system_prompt = ONBOARDING_PROMPT.format(
            today_date=format_local(now_in(tz), tz),
            **biz_data,
        )

        return AssembledContext(
            system_prompt=system_prompt,
            messages=messages,
            tools=get_tools_for_context("onboarding"),
            context_metadata={
                "business_id": str(business_id),
                "context_type": "onboarding",
                "context_id": None,
                "thread_id": thread.id,
            },
        )

    async def _build_business_context(self, business_id: UUID) -> tuple[str, str, str]:
        def load() -> tuple[str, str, str]:
            with Session(engine) as session:
                business = business_service.get_business(session, business_id)
                context = "\n".join(
                    [
                        "BUSINESS:",
                        f"Name: {business.name}",
                        f"GST: {business.gst_number or 'Not registered'}",
                        "Default tax rate: 18%",
                        f"Invoice prefix: {business.invoice_prefix or 'INV'}",
                    ]
                )
                preferred_language = getattr(business, "preferred_language", "hinglish") or "hinglish"
                timezone = getattr(business, "timezone", "Asia/Kolkata") or "Asia/Kolkata"
                return context, preferred_language, timezone

        return await asyncio.to_thread(load)

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

