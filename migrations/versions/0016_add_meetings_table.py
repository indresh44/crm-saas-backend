"""add meetings table

Revision ID: 0016_add_meetings_table
Revises: 0015_quote_invoice_totals
Create Date: 2026-03-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016_add_meetings_table"
down_revision = "0015_quote_invoice_totals"
branch_labels = None
depends_on = None


meeting_status = postgresql.ENUM(
    "scheduled",
    "completed",
    "cancelled",
    "no_show",
    name="meeting_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    meeting_status.create(bind, checkfirst=True)

    op.create_table(
        "meetings",
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", meeting_status, nullable=False, server_default="scheduled"),
        sa.Column("gcal_event_id", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meetings_business_id"), "meetings", ["business_id"], unique=False)
    op.create_index("ix_meetings_business_customer", "meetings", ["business_id", "customer_id"], unique=False)
    op.create_index("ix_meetings_business_scheduled", "meetings", ["business_id", "scheduled_at"], unique=False)
    op.create_index(op.f("ix_meetings_customer_id"), "meetings", ["customer_id"], unique=False)
    op.create_index(op.f("ix_meetings_lead_id"), "meetings", ["lead_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_meetings_lead_id"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_customer_id"), table_name="meetings")
    op.drop_index("ix_meetings_business_scheduled", table_name="meetings")
    op.drop_index("ix_meetings_business_customer", table_name="meetings")
    op.drop_index(op.f("ix_meetings_business_id"), table_name="meetings")
    op.drop_table("meetings")
    meeting_status.drop(op.get_bind(), checkfirst=True)
