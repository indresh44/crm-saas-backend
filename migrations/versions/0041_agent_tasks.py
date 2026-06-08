"""Add agent_tasks table — the multi-task layer above run_agent.

One row per task split from an owner message. Tasks belong to a session, are
grouped by `batch_id` (same value for all tasks split from one message), are
ordered by `sequence_index`, and each own their own `continuity_history` (the
runner gives every task a fresh in-task memory — tasks do NOT share continuity
with each other).

The `agent_chat_sessions.continuity_history` field added by 0040 is left in
place. New traffic flows through this table instead; old sessions created
before 0041 keep their continuity at the session level but it is no longer
read or written by the new chat service path.

Revision ID: 0041_agent_tasks
Revises: 0040_agent_chat_sessions
Create Date: 2026-05-24 00:00:00
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op


revision = "0041_agent_tasks"
down_revision = "0040_agent_chat_sessions"
branch_labels = None
depends_on = None


_STATUS_VALUES = ("queued", "running", "awaiting_approval", "done", "failed")


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "business_id", UUID(as_uuid=True),
            sa.ForeignKey("businesses.id"), nullable=False,
        ),
        sa.Column(
            "session_id", UUID(as_uuid=True),
            sa.ForeignKey("agent_chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id", UUID(as_uuid=True),
            sa.ForeignKey("agent_chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_index", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column(
            "status", sa.String(length=24),
            nullable=False, server_default="queued",
        ),
        sa.Column("result", JSONB, nullable=True),
        sa.Column(
            "continuity_history", JSONB,
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("pending_action_id", sa.String(length=64), nullable=True),
        sa.Column("tokens", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["pending_action_id"], ["prepared_actions.id"],
            name="fk_agent_tasks_pending_action",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")",
            name="ck_agent_tasks_status",
        ),
    )

    # Render + per-batch lookup.
    op.create_index(
        "ix_agent_tasks_session_batch_seq",
        "agent_tasks",
        ["session_id", "batch_id", "sequence_index"],
    )

    # Admin / "in-flight" scans.
    op.create_index(
        "ix_agent_tasks_business_status_updated",
        "agent_tasks",
        ["business_id", "status", sa.text("updated_at DESC")],
    )

    # Direct-tenant index — explicit for tenant-scoped queries that don't carry
    # session_id (admin views, "stuck tasks for this business", etc).
    op.create_index(
        "ix_agent_tasks_business_id",
        "agent_tasks",
        ["business_id"],
    )

    # Partial UNIQUE: at most one task can be holding a given prepared_action_id
    # at a time. Guarantees /confirm's task lookup is single-row.
    op.create_index(
        "uq_agent_tasks_pending_action_id",
        "agent_tasks",
        ["pending_action_id"],
        unique=True,
        postgresql_where=sa.text("pending_action_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_tasks_pending_action_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_business_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_business_status_updated", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_session_batch_seq", table_name="agent_tasks")
    op.drop_table("agent_tasks")
