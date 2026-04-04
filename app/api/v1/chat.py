from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.repositories import chat_message_repo, chat_thread_repo
from app.services.chat_orchestrator import ChatAction, ChatMessageResponse, ChatOrchestrator
from app.services.llm_service import LLMError, LLMRateLimitError, LLMTimeoutError

router = APIRouter()


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    context_type: Literal["dashboard", "customer", "lead", "global"] = "global"
    context_id: UUID | None = None
    thread_id: int | None = None

    @model_validator(mode="after")
    def validate_context(self) -> "ChatMessageRequest":
        if self.context_type in {"customer", "lead"} and self.context_id is None:
            raise ValueError("context_id is required for customer and lead contexts")
        return self


class ChatConfirmRequest(BaseModel):
    thread_id: int
    action_type: str
    confirmed_data: dict[str, Any]


class ChatHistoryMessage(BaseModel):
    id: int
    role: str
    content: str
    created_at: str
    tool_name: str | None = None
    tokens_used: int | None = None


class ChatHistoryResponse(BaseModel):
    thread_id: int
    context_type: str
    context_id: UUID | None = None
    messages: list[ChatHistoryMessage]


class ChatThreadRequest(BaseModel):
    context_type: Literal["dashboard", "customer", "lead", "global"]
    context_id: UUID | None = None

    @model_validator(mode="after")
    def validate_context(self) -> "ChatThreadRequest":
        if self.context_type in {"customer", "lead"} and self.context_id is None:
            raise ValueError("context_id is required for customer and lead contexts")
        return self


class ChatThreadResponse(BaseModel):
    thread_id: int
    context_type: str
    context_id: UUID | None = None
    is_new: bool


@router.post("/chat/message", response_model=ChatMessageResponse)
async def send_chat_message(
    payload: ChatMessageRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatMessageResponse:
    orchestrator = ChatOrchestrator(session=session, current_user=current_user)
    try:
        return await orchestrator.handle_message(
            user_message=payload.message,
            context_type=payload.context_type,
            context_id=payload.context_id,
            thread_id=payload.thread_id,
        )
    except LLMTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="AI assistant is taking too long, please try again",
        ) from exc
    except LLMRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please wait a moment",
        ) from exc
    except LLMError as exc:
        print("CHAT LLM ERROR:", repr(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI assistant encountered an error: abc {str(exc)}",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        print("CHAT UNKNOWN ERROR:", repr(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.post("/chat/confirm", response_model=ChatMessageResponse)
async def confirm_chat_action(
    payload: ChatConfirmRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatMessageResponse:
    orchestrator = ChatOrchestrator(session=session, current_user=current_user)
    return await orchestrator.confirm_action(
        thread_id=payload.thread_id,
        action_type=payload.action_type,
        confirmed_data=payload.confirmed_data,
    )


@router.get("/chat/history/{thread_id}", response_model=ChatHistoryResponse)
def get_chat_history(
    thread_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatHistoryResponse:
    thread = chat_thread_repo.get_by_id(session, thread_id)
    if thread is None or thread.business_id != current_user.business_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )

    messages = chat_message_repo.get_recent(session, thread_id=thread_id, limit=limit)
    return ChatHistoryResponse(
        thread_id=thread.id,
        context_type=thread.context_type,
        context_id=thread.context_id,
        messages=[
            ChatHistoryMessage(
                id=message.id or 0,
                role=message.role,
                content=message.content,
                created_at=message.created_at.isoformat(),
                tool_name=message.tool_name,
                tokens_used=message.tokens_used,
            )
            for message in messages
        ],
    )


@router.post("/chat/thread", response_model=ChatThreadResponse)
def get_or_create_chat_thread(
    payload: ChatThreadRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatThreadResponse:
    existing = chat_thread_repo.get_by_context(
        session=session,
        business_id=current_user.business_id,
        context_type=payload.context_type,
        context_id=payload.context_id,
    )
    thread = chat_thread_repo.get_or_create(
        session=session,
        business_id=current_user.business_id,
        context_type=payload.context_type,
        context_id=payload.context_id,
    )
    return ChatThreadResponse(
        thread_id=thread.id or 0,
        context_type=thread.context_type,
        context_id=thread.context_id,
        is_new=existing is None,
    )
