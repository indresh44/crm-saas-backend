from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.lead import Lead
from app.models.lead_followup import LeadFollowup


def create_lead_followup(session: Session, followup: LeadFollowup) -> LeadFollowup:
    session.add(followup)
    session.commit()
    session.refresh(followup)
    return followup


def get_lead_followup_by_id(
    session: Session,
    followup_id: UUID,
) -> Optional[LeadFollowup]:
    statement = select(LeadFollowup).where(LeadFollowup.id == followup_id)
    return session.exec(statement).first()


def list_followups_for_lead(session: Session, lead_id: UUID) -> List[LeadFollowup]:
    statement = (
        select(LeadFollowup)
        .where(LeadFollowup.lead_id == lead_id)
        .order_by(LeadFollowup.scheduled_at)
    )
    return list(session.exec(statement).all())


def list_followups_for_datetime_range(
    session: Session,
    start_at: datetime,
    end_at: datetime,
) -> List[LeadFollowup]:
    statement = (
        select(LeadFollowup)
        .where(
            LeadFollowup.scheduled_at >= start_at,
            LeadFollowup.scheduled_at < end_at,
            LeadFollowup.status == "pending",
        )
        .order_by(LeadFollowup.scheduled_at)
    )
    return list(session.exec(statement).all())


def list_followups_before_datetime(
    session: Session,
    before_at: datetime,
) -> List[LeadFollowup]:
    statement = (
        select(LeadFollowup)
        .where(
            LeadFollowup.scheduled_at < before_at,
            LeadFollowup.status == "pending",
        )
        .order_by(LeadFollowup.scheduled_at)
    )
    return list(session.exec(statement).all())


def list_followups_with_filters(
    session: Session,
    *,
    business_id: UUID,
    lead_id: UUID | None = None,
    customer_id: UUID | None = None,
    followup_ids: list[UUID] | None = None,
    status: str | None = None,
    date_value: date | None = None,
    before_date: date | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    older_than_days: int | None = None,
    limit: int = 50,
) -> List[LeadFollowup]:
    statement = select(LeadFollowup).join(Lead, Lead.id == LeadFollowup.lead_id).where(Lead.business_id == business_id)

    if lead_id is not None:
        statement = statement.where(LeadFollowup.lead_id == lead_id)
    if customer_id is not None:
        statement = statement.where(Lead.customer_id == customer_id)
    if followup_ids:
        statement = statement.where(LeadFollowup.id.in_(followup_ids))
    if status:
        statement = statement.where(LeadFollowup.status == status)
    if date_value is not None:
        start_at = datetime.combine(date_value, time.min, tzinfo=timezone.utc)
        end_at = start_at + timedelta(days=1)
        statement = statement.where(
            LeadFollowup.scheduled_at >= start_at,
            LeadFollowup.scheduled_at < end_at,
        )
    if before_date is not None:
        cutoff_at = datetime.combine(before_date, time.min, tzinfo=timezone.utc)
        statement = statement.where(LeadFollowup.scheduled_at < cutoff_at)
    if from_date is not None:
        start_at = datetime.combine(from_date, time.min, tzinfo=timezone.utc)
        statement = statement.where(LeadFollowup.scheduled_at >= start_at)
    if to_date is not None:
        end_at = datetime.combine(to_date, time.min, tzinfo=timezone.utc) + timedelta(days=1)
        statement = statement.where(LeadFollowup.scheduled_at < end_at)
    if older_than_days is not None:
        cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=older_than_days)
        cutoff_at = datetime.combine(cutoff_date, time.min, tzinfo=timezone.utc)
        statement = statement.where(LeadFollowup.scheduled_at < cutoff_at)

    statement = statement.order_by(LeadFollowup.scheduled_at).limit(limit)
    return list(session.exec(statement).all())


def update_lead_followup(session: Session, followup: LeadFollowup) -> LeadFollowup:
    session.add(followup)
    session.commit()
    session.refresh(followup)
    return followup
