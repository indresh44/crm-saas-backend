"""invoice status approved + lead source enum

Revision ID: 0022_status_lead_source
Revises: 0021_add_chat_tables
Create Date: 2026-04-07 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_status_lead_source"
down_revision = "0021_add_chat_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Fix 1: invoice_status enum ─────────────────────────────────────────
    # Step 1: migrate existing 'overdue' rows before touching the type
    # Must cast literals to the existing enum type explicitly
    op.execute("""
        UPDATE invoices
        SET status = CASE
            WHEN (
                SELECT COALESCE(SUM(p.amount), 0)
                FROM payments p WHERE p.invoice_id = invoices.id
            ) > 0 THEN 'partial'::invoice_status
            ELSE 'sent'::invoice_status
        END
        WHERE status = 'overdue'::invoice_status
    """)

    # Step 2: rebuild the enum type (PostgreSQL can't DROP VALUE directly)
    op.execute("ALTER TYPE invoice_status RENAME TO invoice_status_old")
    op.execute("CREATE TYPE invoice_status AS ENUM ('draft', 'sent', 'approved', 'partial', 'paid')")
    op.execute("""
        ALTER TABLE invoices
        ALTER COLUMN status TYPE invoice_status
        USING status::text::invoice_status
    """)
    op.execute("DROP TYPE invoice_status_old")

    # ── Fix 5: lead_source enum ────────────────────────────────────────────
    op.execute("""
        CREATE TYPE lead_source AS ENUM (
            'walk_in', 'whatsapp', 'referral', 'instagram', 'justdial', 'website', 'other'
        )
    """)

    # Add new column alongside existing free-text source
    op.add_column("leads", sa.Column("source_enum", sa.Enum(
        "walk_in", "whatsapp", "referral", "instagram", "justdial", "website", "other",
        name="lead_source",
        create_constraint=False,
    ), nullable=True))

    # Migrate known text values to enum
    op.execute("""
        UPDATE leads SET source_enum = CASE
            WHEN LOWER(TRIM(source)) IN ('whatsapp', 'wa', 'wp') THEN 'whatsapp'::lead_source
            WHEN LOWER(TRIM(source)) IN ('referral', 'reference', 'ref') THEN 'referral'::lead_source
            WHEN LOWER(TRIM(source)) IN ('instagram', 'insta', 'ig') THEN 'instagram'::lead_source
            WHEN LOWER(TRIM(source)) IN ('justdial', 'just dial', 'jd') THEN 'justdial'::lead_source
            WHEN LOWER(TRIM(source)) IN ('website', 'web', 'online') THEN 'website'::lead_source
            WHEN LOWER(TRIM(source)) IN ('walk in', 'walk-in', 'walkin', 'walk_in') THEN 'walk_in'::lead_source
            WHEN source IS NOT NULL AND TRIM(source) != '' THEN 'other'::lead_source
            ELSE NULL
        END
        WHERE source IS NOT NULL
    """)

    # Drop old free-text column, rename enum column
    op.drop_column("leads", "source")
    op.alter_column("leads", "source_enum", new_column_name="source")


def downgrade() -> None:
    # ── Fix 5: lead_source enum (reverse) ─────────────────────────────────
    op.add_column("leads", sa.Column("source_text", sa.String(), nullable=True))
    op.execute("UPDATE leads SET source_text = source::text WHERE source IS NOT NULL")
    op.drop_column("leads", "source")
    op.alter_column("leads", "source_text", new_column_name="source")
    op.execute("DROP TYPE lead_source")

    # ── Fix 1: invoice_status enum (reverse) ──────────────────────────────
    op.execute("ALTER TYPE invoice_status RENAME TO invoice_status_old")
    op.execute("CREATE TYPE invoice_status AS ENUM ('draft', 'sent', 'paid', 'partial', 'overdue')")
    op.execute("""
        ALTER TABLE invoices
        ALTER COLUMN status TYPE invoice_status
        USING CASE
            WHEN status::text = 'approved' THEN 'sent'::invoice_status
            ELSE status::text::invoice_status
        END
    """)
    op.execute("DROP TYPE invoice_status_old")
