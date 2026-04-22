"""
Auth endpoints - register, login, logout, refresh.
"""

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from app.core.database import get_session
from app.models.auth_schemas import (
    AuthResponse,
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
)
from app.services.auth_service import (
    confirm_password_reset,
    login,
    logout,
    refresh_access_token,
    register,
    request_password_reset,
)


router = APIRouter()


def _get_device_info(request: Request) -> str | None:
    """Extract device info from User-Agent header for session tracking."""
    user_agent = request.headers.get("User-Agent", "")
    if not user_agent:
        return None
    return user_agent[:500]


@router.post("/auth/register", response_model=AuthResponse, status_code=201)
def register_user(
    payload: RegisterRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> AuthResponse:
    device_info = _get_device_info(request)
    return register(session, payload, device_info)


@router.post("/auth/login", response_model=AuthResponse)
def login_user(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> AuthResponse:
    device_info = _get_device_info(request)
    return login(session, payload, device_info)


@router.post("/auth/refresh")
def refresh_token(
    payload: RefreshRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    device_info = _get_device_info(request)
    return refresh_access_token(session, payload.refresh_token, device_info)


@router.post("/auth/logout", response_model=MessageResponse)
def logout_user(
    payload: RefreshRequest,
    session: Session = Depends(get_session),
) -> MessageResponse:
    logout(session, payload.refresh_token)
    return MessageResponse(message="Logged out successfully")


@router.post("/auth/password-reset/request", response_model=MessageResponse)
def password_reset_request_endpoint(
    payload: PasswordResetRequest,
    session: Session = Depends(get_session),
) -> MessageResponse:
    """
    Request a password reset link. Always returns the same generic success
    message regardless of whether the email matches an account (no user
    enumeration).
    """
    request_password_reset(session, payload.email)
    return MessageResponse(
        message="If that email matches an account, a reset link has been sent."
    )


@router.post("/auth/password-reset/confirm", response_model=MessageResponse)
def password_reset_confirm_endpoint(
    payload: PasswordResetConfirm,
    session: Session = Depends(get_session),
) -> MessageResponse:
    """
    Confirm a password reset. Verifies the token, updates the password, and
    revokes all existing sessions so every device must re-authenticate.
    """
    confirm_password_reset(session, payload.token, payload.new_password)
    return MessageResponse(
        message="Your password has been updated. Please sign in with the new password."
    )
