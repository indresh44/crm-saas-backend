"""
Super-admin gate. Email allowlist via env var, checked against the JWT
on every admin request. Server-side enforcement — UI hiding is not
security.

Even though admin routes are only registered when ENABLE_ADMIN_ROUTES=true,
this dependency is the second line: if the local backend is ever left
running on an open network, the JWT email check still gates access.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.models.user import User


def _allowed_emails() -> set[str]:
    raw = settings.SUPER_ADMIN_EMAILS or ""
    return {email.strip().lower() for email in raw.split(",") if email.strip()}


def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    allowed = _allowed_emails()
    if not allowed:
        # Misconfiguration: admin routes enabled but no admin emails set.
        # Fail closed.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access not configured",
        )

    if (current_user.email or "").strip().lower() not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for admin access",
        )

    return current_user
