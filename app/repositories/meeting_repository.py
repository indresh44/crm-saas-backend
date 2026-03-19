from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from sqlmodel import Session, select

from app.models.enums import MeetingStatus
from app.models.meeting import Meeting


def create_meeting(session: Session, meeting: Meeting) -> Meeting:
    session.add(meeting)
    session.commit()
    session.refresh(meeting)
    return meeting


def get_meeting_by_id(session: Session, business_id: UUID, meeting_id: UUID) -> Meeting | None:
    statement = select(Meeting).where(Meeting.id == meeting_id, Meeting.business_id == business_id)
    return session.exec(statement).first()


def list_meetings(
    session: Session,
    business_id: UUID,
    customer_id: UUID | None = None,
    lead_id: UUID | None = None,
    status: MeetingStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[Meeting]:
    statement = select(Meeting).where(Meeting.business_id == business_id)

    if customer_id is not None:
        statement = statement.where(Meeting.customer_id == customer_id)

    if lead_id is not None:
        statement = statement.where(Meeting.lead_id == lead_id)

    if status is not None:
        statement = statement.where(Meeting.status == status)

    if from_date is not None:
        from_datetime = datetime.combine(from_date, time.min, tzinfo=timezone.utc)
        statement = statement.where(Meeting.scheduled_at >= from_datetime)

    if to_date is not None:
        to_datetime = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        statement = statement.where(Meeting.scheduled_at < to_datetime)

    statement = statement.order_by(Meeting.scheduled_at.asc())
    return list(session.exec(statement).all())


def update_meeting(session: Session, meeting: Meeting) -> Meeting:
    session.add(meeting)
    session.commit()
    session.refresh(meeting)
    return meeting


def delete_meeting(session: Session, meeting: Meeting) -> None:
    session.delete(meeting)
    session.commit()
