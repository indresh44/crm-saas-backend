"""Persistence layer for demand_tags + the enquiry ↔ demand_tag link table.

All callers must pre-normalise tag names (see
`app.services.enquiry_intelligence_service.normalise_demand_name`) — this
layer assumes inputs are already lowercase + trimmed + whitespace-collapsed
so the UNIQUE(business_id, name) constraint stays sane."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlmodel import Session, select

from app.models.demand_tag import DemandTag, DemandTagSummary, EnquiryDemandTag
from app.models.enums import DemandTagOrigin


def get_or_create_demand_tag(
    session: Session,
    *,
    business_id: UUID,
    name: str,
    origin: DemandTagOrigin = DemandTagOrigin.AI,
) -> DemandTag:
    """Idempotent get-or-create on (business_id, name).

    The unique constraint is the source of truth; we do a SELECT first to
    avoid a hot-path INSERT-and-rollback. Caller flushes/commits."""
    existing = session.exec(
        select(DemandTag).where(
            DemandTag.business_id == business_id,
            DemandTag.name == name,
        )
    ).first()
    if existing is not None:
        return existing

    tag = DemandTag(business_id=business_id, name=name, origin=origin)
    session.add(tag)
    session.flush()
    session.refresh(tag)
    return tag


def list_demand_tag_ids_for_enquiry(
    session: Session, enquiry_id: UUID,
) -> list[UUID]:
    stmt = select(EnquiryDemandTag.demand_tag_id).where(
        EnquiryDemandTag.enquiry_id == enquiry_id,
    )
    return list(session.exec(stmt).all())


def get_demand_tags_by_lead_ids(
    session: Session, lead_ids: Sequence[UUID],
) -> dict[UUID, list[DemandTagSummary]]:
    """Bulk-fetch demand_tags grouped by enquiry_id.

    One join query for the whole list view. Tags come back ordered by
    name so chip rows render in a stable order.

    Returns an empty dict when `lead_ids` is empty so list-view callers
    can skip the trip when the page has no rows."""
    if not lead_ids:
        return {}
    stmt = (
        select(EnquiryDemandTag.enquiry_id, DemandTag.id, DemandTag.name)
        .join(DemandTag, DemandTag.id == EnquiryDemandTag.demand_tag_id)
        .where(EnquiryDemandTag.enquiry_id.in_(list(lead_ids)))
        .order_by(DemandTag.name.asc())
    )
    grouped: dict[UUID, list[DemandTagSummary]] = {}
    for enquiry_id, tag_id, tag_name in session.exec(stmt).all():
        grouped.setdefault(enquiry_id, []).append(
            DemandTagSummary(id=tag_id, name=tag_name),
        )
    return grouped


def replace_enquiry_demand_tags(
    session: Session,
    *,
    enquiry_id: UUID,
    demand_tag_ids: Iterable[UUID],
) -> None:
    """Clear-and-rewrite this enquiry's tag links. Idempotent — running it
    twice with the same inputs leaves identical state."""
    session.exec(
        sa_delete(EnquiryDemandTag).where(
            EnquiryDemandTag.enquiry_id == enquiry_id,
        )
    )
    # De-dupe in case the caller passed the same id twice.
    seen: set[UUID] = set()
    for tag_id in demand_tag_ids:
        if tag_id in seen:
            continue
        seen.add(tag_id)
        session.add(
            EnquiryDemandTag(enquiry_id=enquiry_id, demand_tag_id=tag_id),
        )
    session.flush()
