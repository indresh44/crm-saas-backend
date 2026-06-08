"""Agent-chat session + message models.

Two tables. The SESSION owns the loop's persistent in-task memory; the MESSAGE
table is the conversation transcript (user message rows + assistant outcome
rows). Tenant-scoped by `business_id`, like every other entity.

Why two tables — and not one append-only event stream — is in
agent_chat_service.py: the session row must be loaded + locked + updated
atomically around each run_agent call, separately from the message append."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin, utcnow


class AgentChatSession(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, table=True):
    __tablename__ = "agent_chat_sessions"
    __table_args__ = (
        Index(
            "ix_agent_chat_sessions_business_user_updated",
            "business_id", "user_id", "updated_at",
        ),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id", nullable=False)
    user_id: uuid.UUID = Field(foreign_key="users.id", nullable=False)
    title: Optional[str] = Field(default=None, max_length=120, nullable=True)

    # The serialized TurnRecord tuple from the latest run_agent call. Carries
    # the LLM's in-task memory across stateless HTTP requests. Includes
    # observation_raw (needed for UUID provenance); never sent to the client.
    continuity_history: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default="[]"),
    )

    # Non-null iff the last loop call returned awaiting_confirm. The chat
    # service refuses /message until this is cleared (via /confirm or /cancel)
    # so the user can't send a new message while a prepare is mid-air.
    awaiting_action_id: Optional[str] = Field(
        default=None,
        max_length=64,
        sa_column=Column(
            ForeignKey("prepared_actions.id",
                       name="fk_agent_chat_sessions_awaiting_action",
                       ondelete="SET NULL"),
            nullable=True,
        ),
    )


class AgentChatMessage(UUIDPrimaryKeyMixin, table=True):
    __tablename__ = "agent_chat_messages"
    __table_args__ = (
        Index("ix_agent_chat_messages_session_created", "session_id", "created_at"),
    )

    session_id: uuid.UUID = Field(
        sa_column=Column(
            ForeignKey("agent_chat_sessions.id", ondelete="CASCADE"),
            nullable=False, index=False,  # composite index above covers it
        ),
    )
    role: str = Field(max_length=16, nullable=False)   # 'user' | 'assistant'
    kind: str = Field(max_length=24, nullable=False)   # see service.MessageKind
    content: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # Outcome-specific data the renderer keys on (preview, prepared_action_id,
    # editable_fields, question, error dict, commit_result, ...). Never raw
    # observation rows — those live only on the session row.
    payload: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB, nullable=True),
    )

    # Per-message debug snapshot of the NEW turns this message produced (slice
    # of result.history beyond prior_count). Each entry: turn, thought, action,
    # observation_summary. observation_raw is intentionally stripped here.
    turn_detail: Optional[list[dict[str, Any]]] = Field(
        default=None, sa_column=Column(JSONB, nullable=True),
    )

    tokens: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB, nullable=True),
    )

    # Default factory + nullable=False; no UpdatedAtMixin (messages are
    # append-only, never edited).
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
