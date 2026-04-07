from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import uuid

from sqlalchemy import Enum as SaEnum, Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin
from app.models.enums import LeadActivityType, LeadSource


def _lead_activity_type_values(enum_class: type[LeadActivityType]) -> list[str]:
    return [activity_type.value for activity_type in enum_class]


def _lead_source_values(enum_class: type[LeadSource]) -> list[str]:
    return [s.value for s in enum_class]


class LeadFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    customer_id: Optional[uuid.UUID] = None
    stage_id: uuid.UUID
    title: str
    source: Optional[LeadSource] = None
    service_date: Optional[date] = None
    follow_up_at: Optional[datetime] = Field(default=None, nullable=True)
    estimated_value: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    assigned_to: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class LeadBase(LeadFields):
    business_id: uuid.UUID


class LeadCreate(LeadFields):
    pass


class LeadRead(LeadBase):
    id: uuid.UUID
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    stage_name: Optional[str] = None
    stage_color: Optional[str] = None
    created_at: CreatedAtMixin.__annotations__["created_at"]
    updated_at: UpdatedAtMixin.__annotations__["updated_at"]


class LeadUpdate(SQLModel):
    title: Optional[str] = None
    source: Optional[LeadSource] = None
    service_date: Optional[date] = None
    follow_up_at: Optional[datetime] = Field(default=None, nullable=True)
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
    customer_id: Optional[uuid.UUID] = Field(default=None, foreign_key="customers.id")
    stage_id: uuid.UUID = Field(foreign_key="pipeline_stages.id")
    assigned_to: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")
    source: Optional[LeadSource] = Field(
        default=None,
        sa_type=SaEnum(
            LeadSource,
            name="lead_source",
            create_constraint=False,
            values_callable=_lead_source_values,
        ),
        nullable=True,
    )


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
    type: LeadActivityType = Field(
        sa_type=SaEnum(
            LeadActivityType,
            name="lead_activity_type",
            create_constraint=False,
            values_callable=_lead_activity_type_values,
        ),
    )
    created_by: uuid.UUID = Field(foreign_key="users.id", index=True)


class LeadMoveRequest(SQLModel):
    stage_id: uuid.UUID
