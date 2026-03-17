"""msme model updates

Revision ID: 0006_msme_model_updates
Revises: 0005_customer_phone_norm
Create Date: 2026-03-17 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_msme_model_updates"
down_revision = "0005_customer_phone_norm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_followups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lead_followups_business_date",
        "lead_followups",
        ["lead_id", "scheduled_at", "status"],
        unique=False,
    )

    op.alter_column(
        "tasks",
        "lead_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    op.alter_column(
        "invoices",
        "booking_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column("invoices", sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_invoices_lead_id_leads",
        "invoices",
        "leads",
        ["lead_id"],
        ["id"],
    )
    op.create_index(op.f("ix_invoices_lead_id"), "invoices", ["lead_id"], unique=False)

    op.create_table(
        "invoice_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False, server_default=sa.text("1")),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("gst_percent", sa.Numeric(12, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoice_items_invoice_id"), "invoice_items", ["invoice_id"], unique=False)

    op.alter_column(
        "quote_items",
        "name",
        existing_type=sa.String(),
        new_column_name="description",
        existing_nullable=False,
    )
    op.alter_column(
        "quote_items",
        "price",
        existing_type=sa.Numeric(12, 2),
        new_column_name="unit_price",
        existing_nullable=False,
    )
    op.alter_column(
        "quote_items",
        "total",
        existing_type=sa.Numeric(12, 2),
        new_column_name="amount",
        existing_nullable=False,
    )
    op.add_column(
        "quote_items",
        sa.Column("gst_percent", sa.Numeric(12, 2), nullable=True, server_default=sa.text("0")),
    )
    op.add_column(
        "quote_items",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")),
    )
    op.execute("UPDATE quote_items SET gst_percent = 0 WHERE gst_percent IS NULL")
    op.execute("UPDATE quote_items SET created_at = now() WHERE created_at IS NULL")
    op.alter_column(
        "quote_items",
        "gst_percent",
        existing_type=sa.Numeric(12, 2),
        nullable=False,
        server_default=sa.text("0"),
    )
    op.alter_column(
        "quote_items",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "quote_items",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        "quote_items",
        "gst_percent",
        existing_type=sa.Numeric(12, 2),
        nullable=True,
    )
    op.drop_column("quote_items", "created_at")
    op.drop_column("quote_items", "gst_percent")
    op.alter_column(
        "quote_items",
        "amount",
        existing_type=sa.Numeric(12, 2),
        new_column_name="total",
        existing_nullable=False,
    )
    op.alter_column(
        "quote_items",
        "unit_price",
        existing_type=sa.Numeric(12, 2),
        new_column_name="price",
        existing_nullable=False,
    )
    op.alter_column(
        "quote_items",
        "description",
        existing_type=sa.String(),
        new_column_name="name",
        existing_nullable=False,
    )

    op.drop_index(op.f("ix_invoice_items_invoice_id"), table_name="invoice_items")
    op.drop_table("invoice_items")

    op.drop_index(op.f("ix_invoices_lead_id"), table_name="invoices")
    op.drop_constraint("fk_invoices_lead_id_leads", "invoices", type_="foreignkey")
    op.drop_column("invoices", "lead_id")
    op.alter_column(
        "invoices",
        "booking_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.alter_column(
        "tasks",
        "lead_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_index("ix_lead_followups_business_date", table_name="lead_followups")
    op.drop_table("lead_followups")
