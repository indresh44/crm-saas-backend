"""add lead_activity entity type to attachment enum

Revision ID: 0038_lead_act_attach_entity
Revises: 0037_invoice_updated_at
Create Date: 2026-04-29 00:00:00
"""

from alembic import op


revision = "0038_lead_act_attach_entity"
down_revision = "0037_invoice_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE attachment_entity_type ADD VALUE IF NOT EXISTS 'lead_activity'")


def downgrade() -> None:
    # PostgreSQL does not support dropping enum values safely in-place.
    pass
