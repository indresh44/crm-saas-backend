from uuid import UUID

from sqlmodel import Session, select

from app.models.chat import ChatThread
from app.models.common import utcnow


def create(
    session: Session,
    business_id: UUID,
    context_type: str,
    context_id: UUID | None,
) -> ChatThread:
    thread = ChatThread(
        business_id=business_id,
        context_type=context_type,
        context_id=context_id,
    )
    session.add(thread)
    session.commit()
    session.refresh(thread)
    return thread


def get_by_id(
    session: Session,
    thread_id: int,
) -> ChatThread | None:
    statement = select(ChatThread).where(ChatThread.id == thread_id)
    return session.exec(statement).first()


def get_or_create(
    session: Session,
    business_id: UUID,
    context_type: str,
    context_id: UUID | None,
) -> ChatThread:
    thread = get_by_context(
        session=session,
        business_id=business_id,
        context_type=context_type,
        context_id=context_id,
    )

    if thread is not None:
        return thread

    return create(
        session=session,
        business_id=business_id,
        context_type=context_type,
        context_id=context_id,
    )


def get_by_context(
    session: Session,
    business_id: UUID,
    context_type: str,
    context_id: UUID | None,
) -> ChatThread | None:
    statement = (
        select(ChatThread)
        .where(
            ChatThread.business_id == business_id,
            ChatThread.context_type == context_type,
            ChatThread.context_id == context_id,
        )
        .order_by(ChatThread.updated_at.desc())
    )
    return session.exec(statement).first()


def update_summary(
    session: Session,
    thread_id: int,
    summary: str,
) -> ChatThread | None:
    thread = get_by_id(session=session, thread_id=thread_id)
    if thread is None:
        return None

    thread.summary = summary
    thread.updated_at = utcnow()
    session.add(thread)
    session.commit()
    session.refresh(thread)
    return thread


def get_recent_threads(
    session: Session,
    business_id: UUID,
    limit: int = 10,
) -> list[ChatThread]:
    statement = (
        select(ChatThread)
        .where(ChatThread.business_id == business_id)
        .order_by(ChatThread.updated_at.desc())
        .limit(limit)
    )
    return list(session.exec(statement).all())
