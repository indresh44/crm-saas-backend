from typing import Any

from sqlalchemy import delete, func
from sqlmodel import Session, select

from app.models.chat import ChatMessage, ChatThread
from app.models.common import utcnow


def create(
    session: Session,
    thread_id: int,
    role: str,
    content: str,
    tool_name: str | None = None,
    tool_input: dict[str, Any] | None = None,
    tool_output: dict[str, Any] | None = None,
    tokens_used: int | None = None,
) -> ChatMessage:
    message = ChatMessage(
        thread_id=thread_id,
        role=role,
        content=content,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        tokens_used=tokens_used,
    )
    session.add(message)

    thread = session.get(ChatThread, thread_id)
    if thread is not None:
        thread.updated_at = utcnow()
        session.add(thread)

    session.commit()
    session.refresh(message)
    return message


def get_recent(
    session: Session,
    thread_id: int,
    limit: int = 10,
) -> list[ChatMessage]:
    recent_message_ids = (
        select(ChatMessage.id)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
        .subquery()
    )

    statement = (
        select(ChatMessage)
        .where(ChatMessage.id.in_(select(recent_message_ids.c.id)))
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    return list(session.exec(statement).all())


def count(
    session: Session,
    thread_id: int,
) -> int:
    statement = select(func.count(ChatMessage.id)).where(ChatMessage.thread_id == thread_id)
    return int(session.exec(statement).one())


def get_older_than_recent(
    session: Session,
    thread_id: int,
    recent_limit: int = 10,
) -> list[ChatMessage]:
    recent_message_ids = (
        select(ChatMessage.id)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(recent_limit)
        .subquery()
    )

    statement = (
        select(ChatMessage)
        .where(
            ChatMessage.thread_id == thread_id,
            ~ChatMessage.id.in_(select(recent_message_ids.c.id)),
        )
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    return list(session.exec(statement).all())


def delete_older_than_recent(
    session: Session,
    thread_id: int,
    recent_limit: int = 10,
) -> int:
    older_messages = get_older_than_recent(
        session=session,
        thread_id=thread_id,
        recent_limit=recent_limit,
    )
    if not older_messages:
        return 0

    older_message_ids = [message.id for message in older_messages if message.id is not None]
    if not older_message_ids:
        return 0

    result = session.exec(
        delete(ChatMessage).where(ChatMessage.id.in_(older_message_ids))
    )

    thread = session.get(ChatThread, thread_id)
    if thread is not None:
        thread.updated_at = utcnow()
        session.add(thread)

    session.commit()
    return int(result.rowcount or 0)
