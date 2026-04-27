"""
Admin audit log endpoint — read-only history of every admin action.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.admin.dependencies import require_super_admin
from app.core.database import get_session
from app.models.admin_audit_log import AdminAuditLogRead
from app.models.user import User
from app.repositories.admin_audit_repository import list_audit


router = APIRouter()


@router.get("", response_model=list[AdminAuditLogRead])
def admin_list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    _admin: User = Depends(require_super_admin),
) -> list[AdminAuditLogRead]:
    rows = list_audit(session, limit=limit, offset=offset)
    return [AdminAuditLogRead.model_validate(r, from_attributes=True) for r in rows]
