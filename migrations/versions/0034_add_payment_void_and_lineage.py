"""Add payment void + lineage fields and payment activity enum values

Adds void/audit columns to `payments`:
- voided_at, voided_reason, voided_by — soft-void state
- replaces_payment_id — lineage when an amount-edit voids old + creates new
- edited_at — last in-place metadata change

Also extends the Postgres `lead_activity_type` enum with three new values
used by the payment edit/void flow.

Revision ID: 0034_payment_void_lineage
Revises: 0033_cancelled_enum_value
Create Date: 2026-04-26 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0034_payment_void_lineage"
down_revision = "0033_cancelled_enum_value"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("voided_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("voided_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("replaces_payment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_payments_voided_by_users",
        "payments",
        "users",
        ["voided_by"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_payments_replaces_payment_id",
        "payments",
        "payments",
        ["replaces_payment_id"],
        ["id"],
    )

    op.create_index(
        "ix_payments_voided_at",
        "payments",
        ["voided_at"],
        unique=False,
    )
    op.create_index(
        "ix_payments_replaces_payment_id",
        "payments",
        ["replaces_payment_id"],
        unique=False,
    )

    # Activity enum values for the lead activity feed.
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'payment_edited'")
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'payment_voided'")
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'payment_moved'")


def downgrade() -> None:
    op.drop_index("ix_payments_replaces_payment_id", table_name="payments")
    op.drop_index("ix_payments_voided_at", table_name="payments")
    op.drop_constraint("fk_payments_replaces_payment_id", "payments", type_="foreignkey")
    op.drop_constraint("fk_payments_voided_by_users", "payments", type_="foreignkey")
    op.drop_column("payments", "edited_at")
    op.drop_column("payments", "replaces_payment_id")
    op.drop_column("payments", "voided_by")
    op.drop_column("payments", "voided_reason")
    op.drop_column("payments", "voided_at")
    # Postgres does not support removing values from an enum type. Leaving
    # the new values in place on downgrade is the conventional tradeoff.
