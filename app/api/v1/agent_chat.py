"""HTTP surface for the agent-chat session.

NEW router, separate from /api/v1/chat. Do not extend the old chat_orchestrator
from here — that is a different surface with a different action model.

Endpoints (all under /api/v1/agent-chat):
    POST   /sessions                          create
    GET    /sessions                          list (most recent first)
    GET    /sessions/{id}                     fetch session + messages
    POST   /sessions/{id}/message             send -> uniform assistant envelope
    POST   /sessions/{id}/confirm             commit the awaiting prepare
    POST   /sessions/{id}/cancel              decline the awaiting prepare

Auth: standard `get_current_user` dependency. The X-User-Id fallback is
inherited from that dependency but NOT relied on here — every handler
extracts both business_id and user_id from the resolved User."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.services import agent_chat_service
from app.services.llm_service import LLMError, LLMRateLimitError, LLMTimeoutError


router = APIRouter(prefix="/agent-chat")


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)


class SessionSummary(BaseModel):
    id: UUID
    title: Optional[str]
    updated_at: str
    awaiting_action_id: Optional[str]


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]


class MessageRow(BaseModel):
    id: UUID
    role: str
    kind: str
    content: Optional[str]
    payload: Optional[dict[str, Any]]
    turn_detail: Optional[list[dict[str, Any]]]
    tokens: Optional[dict[str, Any]]
    created_at: str


class SessionDetailResponse(BaseModel):
    id: UUID
    title: Optional[str]
    awaiting_action_id: Optional[str]
    updated_at: str
    messages: list[MessageRow]


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ConfirmRequest(BaseModel):
    prepared_action_id: str = Field(min_length=1, max_length=64)
    edits: Optional[dict[str, Any]] = None


class CancelRequest(BaseModel):
    prepared_action_id: str = Field(min_length=1, max_length=64)


class AssistantEnvelopeResponse(BaseModel):
    session_id: UUID
    message_id: UUID
    kind: str
    content: Optional[str]
    payload: dict[str, Any]
    turn_detail: list[dict[str, Any]]
    tokens: Optional[dict[str, Any]]
    created_at: str
    awaiting_action_id: Optional[str]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@router.post("/sessions", response_model=SessionSummary, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: CreateSessionRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SessionSummary:
    row = agent_chat_service.create_session(session, current_user, title=payload.title)
    session.commit()
    return SessionSummary(
        id=row.id, title=row.title,
        updated_at=row.updated_at.isoformat(),
        awaiting_action_id=row.awaiting_action_id,
    )


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SessionListResponse:
    rows = agent_chat_service.list_sessions(session, current_user, limit=limit)
    return SessionListResponse(sessions=[
        SessionSummary(
            id=r.id, title=r.title,
            updated_at=r.updated_at.isoformat(),
            awaiting_action_id=r.awaiting_action_id,
        )
        for r in rows
    ])


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session_detail(
    session_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SessionDetailResponse:
    row, messages = agent_chat_service.get_session_with_messages(
        session, current_user, session_id,
    )
    return SessionDetailResponse(
        id=row.id, title=row.title,
        awaiting_action_id=row.awaiting_action_id,
        updated_at=row.updated_at.isoformat(),
        messages=[
            MessageRow(
                id=m.id, role=m.role, kind=m.kind, content=m.content,
                payload=m.payload, turn_detail=m.turn_detail, tokens=m.tokens,
                created_at=m.created_at.isoformat(),
            )
            for m in messages
        ],
    )


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

@router.post("/sessions/{session_id}/message", response_model=AssistantEnvelopeResponse)
async def send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AssistantEnvelopeResponse:
    try:
        env = await agent_chat_service.handle_message(
            session, current_user,
            session_id=session_id, message_text=payload.message,
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
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI assistant error: {exc}",
        ) from exc
    return AssistantEnvelopeResponse(**env.to_dict())


@router.post("/sessions/{session_id}/confirm", response_model=AssistantEnvelopeResponse)
def confirm(
    session_id: UUID,
    payload: ConfirmRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AssistantEnvelopeResponse:
    env = agent_chat_service.handle_confirm(
        session, current_user,
        session_id=session_id,
        prepared_action_id=payload.prepared_action_id,
        edits=payload.edits or {},
    )
    return AssistantEnvelopeResponse(**env.to_dict())


@router.post("/sessions/{session_id}/cancel", response_model=AssistantEnvelopeResponse)
def cancel(
    session_id: UUID,
    payload: CancelRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AssistantEnvelopeResponse:
    env = agent_chat_service.handle_cancel(
        session, current_user,
        session_id=session_id,
        prepared_action_id=payload.prepared_action_id,
    )
    return AssistantEnvelopeResponse(**env.to_dict())
