"""Add updated_at column to invoices

Adds an `updated_at` timestamp to the invoices table that auto-bumps
on every row update via SQLAlchemy's `onupdate` (configured at the
ORM layer in app/models/common.py:UpdatedAtMixin).

Existing rows get the migration's run timestamp via `server_default`,
so all existing invoices appear "freshly updated" once the migration
runs — that's fine and means their next share will get a fresh OG
preview from any social-media link cache.

Why we need this: WhatsApp and other platforms cache OG link previews
by URL. The frontend appends `?v={updated_at_unix}` to share URLs
while the invoice is still editable (draft/sent), so each edit busts
the cache. Once approved, the URL goes back to canonical (no `?v=`).

Revision ID: 0037_invoice_updated_at
Revises: 0036_admin_audit_log
Create Date: 2026-04-29 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_invoice_updated_at"
down_revision = "0036_admin_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("invoices", "updated_at")
