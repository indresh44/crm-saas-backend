"""
Admin audit log repository — write actions, read history.
"""

from __future__ import annotations

from typing import Any, Optional
import uuid

from sqlalchemy import desc
from sqlmodel import Session, select

from app.models.admin_audit_log import AdminAuditLog


def write_audit(
    session: Session,
    *,
    admin_email: str,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[uuid.UUID] = None,
    before_snapshot: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AdminAuditLog:
    entry = AdminAuditLog(
        admin_email=admin_email,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before_snapshot=before_snapshot,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def list_audit(
    session: Session,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[AdminAuditLog]:
    statement = (
        select(AdminAuditLog)
        .order_by(desc(AdminAuditLog.created_at))
        .limit(limit)
        .offset(offset)
    )
    return list(session.exec(statement).all())
