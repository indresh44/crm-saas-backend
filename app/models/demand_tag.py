from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, Enum as SaEnum, UniqueConstraint, text as sa_text
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import DemandTagOrigin


def _demand_tag_origin_values(enum_class: type[DemandTagOrigin]) -> list[str]:
    return [v.value for v in enum_class]


class DemandTagBase(SQLModel):
    business_id: uuid.UUID
    name: str  # always stored lowercase + trimmed + whitespace-collapsed
    origin: DemandTagOrigin = Field(default=DemandTagOrigin.AI)


class DemandTagRead(DemandTagBase):
    id: uuid.UUID
    created_at: datetime


class DemandTagSummary(SQLModel):
    """Minimal shape returned alongside a lead — just enough for chip
    rendering. `name` is the stored lowercase canonical form; the UI
    Title Cases it at render time without touching storage."""

    id: uuid.UUID
    name: str


class DemandTag(DemandTagBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    """One row per (business, normalised demand phrase). Reused across many
    enquiries via the `enquiry_demand_tags` link table."""

    __tablename__ = "demand_tags"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "name", name="uq_demand_tags_business_name",
        ),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id")
    origin: DemandTagOrigin = Field(
        default=DemandTagOrigin.AI,
        sa_column=Column(
            SaEnum(
                DemandTagOrigin,
                name="demand_tag_origin",
                create_constraint=False,
                values_callable=_demand_tag_origin_values,
            ),
            nullable=False,
            server_default=sa_text("'ai'::demand_tag_origin"),
        ),
    )


class EnquiryDemandTag(SQLModel, table=True):
    """Many-to-many link: enquiry (lead) ↔ demand_tag.

    PK is the composite (enquiry_id, demand_tag_id). On enquiry delete the
    rows cascade; on demand_tag delete they cascade too — orphan link rows
    are never useful."""

    __tablename__ = "enquiry_demand_tags"

    enquiry_id: uuid.UUID = Field(
        foreign_key="leads.id", primary_key=True,
    )
    demand_tag_id: uuid.UUID = Field(
        foreign_key="demand_tags.id", primary_key=True,
    )
