"""Data-access for agent_chat_sessions + agent_chat_messages.

Tenant-scoped. The service layer always passes business_id (and usually
user_id) explicitly — same convention as every other repository here. The
LOCK helper (`get_session_for_update`) is the chokepoint that serialises
concurrent /message and /confirm against the same session row."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.agent_chat import AgentChatMessage, AgentChatSession


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(
    session: Session, *, business_id: UUID, user_id: UUID,
    title: Optional[str] = None,
) -> AgentChatSession:
    row = AgentChatSession(
        business_id=business_id, user_id=user_id, title=title,
        continuity_history=[], awaiting_action_id=None,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def get_session(
    session: Session, *, session_id: UUID, business_id: UUID, user_id: UUID,
) -> Optional[AgentChatSession]:
    stmt = select(AgentChatSession).where(
        AgentChatSession.id == session_id,
        AgentChatSession.business_id == business_id,
        AgentChatSession.user_id == user_id,
    )
    return session.exec(stmt).first()


def get_session_for_update(
    session: Session, *, session_id: UUID, business_id: UUID, user_id: UUID,
) -> Optional[AgentChatSession]:
    """Tenant-scoped + row-locked. The service holds this lock for the
    duration of a /message or /confirm call, which serialises concurrent
    requests against the same session (no race on continuity_history)."""
    stmt = (
        select(AgentChatSession)
        .where(
            AgentChatSession.id == session_id,
            AgentChatSession.business_id == business_id,
            AgentChatSession.user_id == user_id,
        )
        .with_for_update()
    )
    return session.exec(stmt).first()


def list_sessions(
    session: Session, *, business_id: UUID, user_id: UUID, limit: int = 50,
) -> list[AgentChatSession]:
    stmt = (
        select(AgentChatSession)
        .where(
            AgentChatSession.business_id == business_id,
            AgentChatSession.user_id == user_id,
        )
        .order_by(AgentChatSession.updated_at.desc())
        .limit(limit)
    )
    return list(session.exec(stmt).all())


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def add_message(
    session: Session, *,
    session_id: UUID,
    role: str,
    kind: str,
    content: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    turn_detail: Optional[list[dict[str, Any]]] = None,
    tokens: Optional[dict[str, Any]] = None,
) -> AgentChatMessage:
    row = AgentChatMessage(
        session_id=session_id,
        role=role, kind=kind, content=content,
        payload=payload, turn_detail=turn_detail, tokens=tokens,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def list_messages(
    session: Session, *, session_id: UUID, limit: int = 200,
) -> list[AgentChatMessage]:
    stmt = (
        select(AgentChatMessage)
        .where(AgentChatMessage.session_id == session_id)
        .order_by(AgentChatMessage.created_at.asc(), AgentChatMessage.id.asc())
        .limit(limit)
    )
    return list(session.exec(stmt).all())
