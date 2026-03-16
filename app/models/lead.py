from datetime import date
from decimal import Decimal
from typing import Optional
import uuid

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin
from app.models.enums import LeadActivityType


class LeadFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    customer_id: uuid.UUID
    stage_id: uuid.UUID
    title: str
    source: Optional[str] = None
    event_date: Optional[date] = None
    estimated_value: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    assigned_to: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class LeadBase(LeadFields):
    business_id: uuid.UUID


class LeadCreate(LeadFields):
    pass


class LeadRead(LeadBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]
    updated_at: UpdatedAtMixin.__annotations__["updated_at"]


class LeadUpdate(SQLModel):
    title: Optional[str] = None
    source: Optional[str] = None
    event_date: Optional[date] = None
    estimated_value: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    assigned_to: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class Lead(LeadBase, UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, table=True):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_business_stage", "business_id", "stage_id"),
        Index("ix_leads_business_created", "business_id", "created_at"),
        Index("ix_leads_business_customer", "business_id", "customer_id"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id")
    customer_id: uuid.UUID = Field(foreign_key="customers.id")
    stage_id: uuid.UUID = Field(foreign_key="pipeline_stages.id")
    assigned_to: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")


class LeadActivityBase(SQLModel):
    lead_id: uuid.UUID
    type: LeadActivityType
    description: str
    created_by: uuid.UUID


class LeadActivityCreate(LeadActivityBase):
    pass


class LeadActivityRead(LeadActivityBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class LeadActivity(LeadActivityBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "lead_activities"

    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    created_by: uuid.UUID = Field(foreign_key="users.id", index=True)


class LeadMoveRequest(SQLModel):
    stage_id: uuid.UUID
