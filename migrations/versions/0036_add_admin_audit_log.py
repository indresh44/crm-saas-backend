"""Add admin_audit_log table

Revision ID: 0036_admin_audit_log
Revises: 0035_welcome_email_sent_at
Create Date: 2026-04-26 00:00:00
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision = "0036_admin_audit_log"
down_revision = "0035_welcome_email_sent_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("admin_email", sa.String(length=320), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column("before_snapshot", JSONB, nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_admin_audit_log_admin_email",
        "admin_audit_log",
        ["admin_email"],
    )
    op.create_index(
        "ix_admin_audit_log_action",
        "admin_audit_log",
        ["action"],
    )
    op.create_index(
        "ix_admin_audit_log_target_id",
        "admin_audit_log",
        ["target_id"],
    )
    op.create_index(
        "ix_admin_audit_log_created_at",
        "admin_audit_log",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_admin_audit_log_created_at", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_target_id", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_action", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_admin_email", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
