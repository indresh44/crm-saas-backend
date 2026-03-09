"""create customers table

Revision ID: 0001_create_customers_table
Revises:
Create Date: 2026-03-09 02:03:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_create_customers_table"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_customer_business_id"), "customer", ["business_id"], unique=False)
    op.create_index(op.f("ix_customer_phone"), "customer", ["phone"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_customer_phone"), table_name="customer")
    op.drop_index(op.f("ix_customer_business_id"), table_name="customer")
    op.drop_table("customer")
