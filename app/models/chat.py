from typing import Any
import uuid

from sqlalchemy import Index
from sqlmodel import Field, JSON, SQLModel

from app.models.common import CreatedAtMixin, UpdatedAtMixin


class ChatThreadBase(SQLModel):
    business_id: uuid.UUID
    context_type: str = Field(max_length=20)
    context_id: uuid.UUID | None = Field(default=None, nullable=True)
    summary: str | None = Field(default=None, nullable=True)


class ChatMessageBase(SQLModel):
    thread_id: int
    role: str = Field(max_length=15)
    content: str
    tool_name: str | None = Field(default=None, max_length=50, nullable=True)
    tool_input: dict[str, Any] | None = None
    tool_output: dict[str, Any] | None = None
    tokens_used: int | None = Field(default=None, nullable=True)


class ChatThread(ChatThreadBase, CreatedAtMixin, UpdatedAtMixin, table=True):
    __tablename__ = "chat_threads"
    __table_args__ = (
        Index(
            "ix_chat_threads_business_context",
            "business_id",
            "context_type",
            "context_id",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)


class ChatMessage(ChatMessageBase, CreatedAtMixin, table=True):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_thread_created", "thread_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    thread_id: int = Field(foreign_key="chat_threads.id", index=True)
    tool_input: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    tool_output: dict[str, Any] | None = Field(default=None, sa_type=JSON)
