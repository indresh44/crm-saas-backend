from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.customer import Customer
from app.models.enums import MeetingStatus
from app.models.lead import Lead
from app.models.meeting import Meeting, MeetingRead


def create_meeting(session: Session, meeting: Meeting) -> Meeting:
    session.add(meeting)
    session.commit()
    session.refresh(meeting)
    return meeting


def get_meeting_by_id(session: Session, business_id: UUID, meeting_id: UUID) -> Meeting | None:
    statement = select(Meeting).where(Meeting.id == meeting_id, Meeting.business_id == business_id)
    return session.exec(statement).first()


def _meeting_read_from_row(
    meeting: Meeting,
    customer_name: str | None,
    customer_phone: str | None,
    lead_title: str | None,
) -> MeetingRead:
    meeting_read = MeetingRead.model_validate(meeting, from_attributes=True)
    meeting_read.customer_name = customer_name
    meeting_read.customer_phone = customer_phone
    meeting_read.lead_title = lead_title
    return meeting_read


def get_meeting_by_id_enriched(
    session: Session,
    business_id: UUID,
    meeting_id: UUID,
) -> MeetingRead | None:
    statement = (
        select(
            Meeting,
            Customer.name,
            Customer.phone,
            Lead.title,
        )
        .join(Customer, Meeting.customer_id == Customer.id)
        .outerjoin(Lead, Meeting.lead_id == Lead.id)
        .where(Meeting.business_id == business_id, Meeting.id == meeting_id)
    )

    row = session.exec(statement).first()
    if row is None:
        return None

    meeting, customer_name, customer_phone, lead_title = row
    return _meeting_read_from_row(meeting, customer_name, customer_phone, lead_title)


def list_meetings(
    session: Session,
    business_id: UUID,
    customer_id: UUID | None = None,
    lead_id: UUID | None = None,
    status: MeetingStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[MeetingRead], int]:
    statement = (
        select(
            Meeting,
            Customer.name,
            Customer.phone,
            Lead.title,
        )
        .join(Customer, Meeting.customer_id == Customer.id)
        .outerjoin(Lead, Meeting.lead_id == Lead.id)
        .where(Meeting.business_id == business_id)
    )

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

    total = session.exec(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).one()

    rows = session.exec(
        statement.order_by(Meeting.scheduled_at.asc()).offset(offset).limit(limit)
    ).all()

    meetings = [
        _meeting_read_from_row(meeting, customer_name, customer_phone, lead_title)
        for meeting, customer_name, customer_phone, lead_title in rows
    ]
    return meetings, total


def update_meeting(session: Session, meeting: Meeting) -> Meeting:
    session.add(meeting)
    session.commit()
    session.refresh(meeting)
    return meeting


def delete_meeting(session: Session, meeting: Meeting) -> None:
    session.delete(meeting)
    session.commit()
