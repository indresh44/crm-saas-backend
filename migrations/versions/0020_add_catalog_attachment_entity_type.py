"""add catalog entity type to attachment enum

Revision ID: 0020_catalog_attachment_entity
Revises: 0019_invoice_pdf_fields
Create Date: 2026-03-21 00:00:00
"""

from alembic import op


revision = "0020_catalog_attachment_entity"
down_revision = "0019_invoice_pdf_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE attachment_entity_type ADD VALUE IF NOT EXISTS 'catalog'")


def downgrade() -> None:
    # PostgreSQL does not support dropping enum values safely in-place.
    pass
