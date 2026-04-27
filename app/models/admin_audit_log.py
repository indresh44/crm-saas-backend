"""
Admin audit log — every action a super-admin takes is recorded here.

Lives in the prod DB so the trail persists across local-backend restarts,
but only ever written to by the local admin process (production never
registers the admin router).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
import uuid

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


class AdminAuditLogBase(SQLModel):
    admin_email: str = Field(max_length=320, nullable=False, index=True)
    action: str = Field(max_length=64, nullable=False, index=True)
    target_type: Optional[str] = Field(default=None, max_length=64, nullable=True)
    target_id: Optional[uuid.UUID] = Field(default=None, nullable=True, index=True)
    ip_address: Optional[str] = Field(default=None, max_length=64, nullable=True)
    user_agent: Optional[str] = Field(default=None, max_length=500, nullable=True)


class AdminAuditLog(AdminAuditLogBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "admin_audit_log"

    before_snapshot: Optional[dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )


class AdminAuditLogRead(AdminAuditLogBase):
    id: uuid.UUID
    before_snapshot: Optional[dict[str, Any]] = None
    created_at: datetime
