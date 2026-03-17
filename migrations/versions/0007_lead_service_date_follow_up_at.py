"""lead service date and follow up at

Revision ID: 0007_lead_service_followup
Revises: 0006_msme_model_updates
Create Date: 2026-03-17 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_lead_service_followup"
down_revision = "0006_msme_model_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "leads",
        "event_date",
        existing_type=sa.Date(),
        new_column_name="service_date",
        existing_nullable=True,
    )
    op.add_column("leads", sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "follow_up_at")
    op.alter_column(
        "leads",
        "service_date",
        existing_type=sa.Date(),
        new_column_name="event_date",
        existing_nullable=True,
    )
