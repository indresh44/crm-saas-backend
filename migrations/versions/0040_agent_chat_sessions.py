"""Add agent_chat_sessions + agent_chat_messages tables.

The agent loop's Stage-1 in-task memory (TurnRecord tuple) lives on a session
row in `continuity_history`. Each user/assistant exchange writes a message row;
the assistant message's `turn_detail` is a debug snapshot of the new turns this
message produced (without observation_raw — raw stays on the session row for
UUID provenance only).

Revision ID: 0040_agent_chat_sessions
Revises: 0039_prepared_actions
Create Date: 2026-05-23 00:00:00
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op


revision = "0040_agent_chat_sessions"
down_revision = "0039_prepared_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("continuity_history", JSONB,
                  nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("awaiting_action_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["awaiting_action_id"], ["prepared_actions.id"],
            name="fk_agent_chat_sessions_awaiting_action",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_agent_chat_sessions_business_user_updated",
        "agent_chat_sessions",
        ["business_id", "user_id", sa.text("updated_at DESC")],
    )

    op.create_table(
        "agent_chat_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("session_id", UUID(as_uuid=True),
                  sa.ForeignKey("agent_chat_sessions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("turn_detail", JSONB, nullable=True),
        sa.Column("tokens", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_agent_chat_messages_session_created",
        "agent_chat_messages",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_chat_messages_session_created",
                  table_name="agent_chat_messages")
    op.drop_table("agent_chat_messages")
    op.drop_index("ix_agent_chat_sessions_business_user_updated",
                  table_name="agent_chat_sessions")
    op.drop_table("agent_chat_sessions")
