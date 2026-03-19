"""restructure quote and invoice totals and add quote template flag

Revision ID: 0015_quote_invoice_totals
Revises: 0014_quote_title_null
Create Date: 2026-03-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0015_quote_invoice_totals"
down_revision = "0014_quote_title_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    quote_columns = {column["name"] for column in inspector.get_columns("quotes")}
    invoice_columns = {column["name"] for column in inspector.get_columns("invoices")}

    if "is_template" not in quote_columns:
        op.add_column(
            "quotes",
            sa.Column("is_template", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if "subtotal" not in quote_columns:
        op.add_column(
            "quotes",
            sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0"),
        )
    if "tax_total" not in quote_columns:
        op.add_column(
            "quotes",
            sa.Column("tax_total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        )

    if "subtotal" not in invoice_columns:
        op.add_column(
            "invoices",
            sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0"),
        )
    if "tax_total" not in invoice_columns:
        op.add_column(
            "invoices",
            sa.Column("tax_total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        )

    op.execute("UPDATE quotes SET subtotal = total_amount, tax_total = 0")
    op.execute("UPDATE invoices SET subtotal = total_amount, tax_total = 0")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    quote_columns = {column["name"] for column in inspector.get_columns("quotes")}
    invoice_columns = {column["name"] for column in inspector.get_columns("invoices")}

    if "tax_total" in invoice_columns:
        op.drop_column("invoices", "tax_total")
    if "subtotal" in invoice_columns:
        op.drop_column("invoices", "subtotal")

    if "tax_total" in quote_columns:
        op.drop_column("quotes", "tax_total")
    if "subtotal" in quote_columns:
        op.drop_column("quotes", "subtotal")
    if "is_template" in quote_columns:
        op.drop_column("quotes", "is_template")
