"""
Auth endpoints - register, login, logout, refresh.
"""

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from app.core.database import get_session
from app.models.auth_schemas import AuthResponse, LoginRequest, MessageResponse, RefreshRequest, RegisterRequest
from app.services.auth_service import login, logout, refresh_access_token, register


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
