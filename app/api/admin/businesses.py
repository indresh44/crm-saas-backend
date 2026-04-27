"""
Admin business endpoints — list / detail / delete.
"""

from __future__ import annotations

from typing import Any
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlmodel import Session

from app.api.admin.dependencies import require_super_admin
from app.core.database import get_session
from app.models.user import User
from app.services.admin_business_service import (
    delete_business_cascade,
    get_business_detail,
    list_businesses,
)


router = APIRouter()


@router.get("")
def admin_list_businesses(
    session: Session = Depends(get_session),
    _admin: User = Depends(require_super_admin),
) -> list[dict[str, Any]]:
    return list_businesses(session)


@router.get("/{business_id}")
def admin_get_business(
    business_id: uuid.UUID,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_super_admin),
) -> dict[str, Any]:
    detail = get_business_detail(session, business_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")
    return detail


@router.delete("/{business_id}", status_code=status.HTTP_200_OK)
def admin_delete_business(
    business_id: uuid.UUID,
    request: Request,
    payload: dict = Body(default_factory=dict),
    session: Session = Depends(get_session),
    admin: User = Depends(require_super_admin),
) -> dict[str, Any]:
    """
    Hard-delete a business and every child record. Caller must POST a
    body containing `confirm_name` matching the business's exact name —
    fat-finger insurance.
    """
    confirm_name = (payload.get("confirm_name") or "").strip()
    if not confirm_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm_name is required",
        )

    detail = get_business_detail(session, business_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")

    if confirm_name != detail["name"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"confirm_name does not match business name '{detail['name']}'",
        )

    snapshot = delete_business_cascade(
        session,
        business_id=business_id,
        admin_email=admin.email,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
    )
    return {"deleted": True, "snapshot": snapshot}
