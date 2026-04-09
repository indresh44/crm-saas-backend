"""
Auth service - register, login, logout, refresh token.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlmodel import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.auth_identity import AuthIdentity
from app.models.auth_schemas import AuthResponse, LoginRequest, RegisterRequest
from app.models.business import Business
from app.models.enums import AuthProvider, UserRole
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.auth_repository import (
    create_refresh_token,
    get_identity_by_provider,
    get_refresh_token_by_hash,
    revoke_all_user_tokens,
    revoke_refresh_token,
)
from app.repositories.business_repository import get_business_by_id
from app.repositories.user_repository import get_user_by_id
from app.services.pipeline_service import create_default_pipeline, ensure_pipeline_exists


def _issue_tokens(
    session: Session,
    user: User,
    device_info: str | None = None,
) -> dict[str, str]:
    """
    Issue an access token + refresh token pair for a user.
    """
    role_value = user.role.value if hasattr(user.role, "value") else str(user.role)
    access_token = create_access_token(
        user_id=user.id,
        business_id=user.business_id,
        role=role_value,
    )

    raw_refresh_token = generate_refresh_token()
    token_hash = hash_refresh_token(raw_refresh_token)

    refresh_token_record = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        device_info=device_info,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        is_revoked=False,
    )
    create_refresh_token(session, refresh_token_record)

    return {
        "access_token": access_token,
        "refresh_token": raw_refresh_token,
    }


def _build_auth_response(
    user: User,
    business: Business,
    tokens: dict[str, str],
) -> AuthResponse:
    """Build the standard auth response."""
    role_value = user.role.value if hasattr(user.role, "value") else str(user.role)
    return AuthResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type="bearer",
        user={
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "role": role_value,
            "business_id": str(user.business_id),
        },
        business={
            "id": str(business.id),
            "name": business.name,
            "onboarding_status": business.onboarding_status,
        },
    )


def register(
    session: Session,
    data: RegisterRequest,
    device_info: str | None = None,
) -> AuthResponse:
    """
    Register a new user with email + password.
    """
    email = data.email.strip().lower()

    existing = get_identity_by_provider(session, AuthProvider.EMAIL, email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists",
        )

    phone_full = f"{data.country_code}{data.phone}" if data.phone else ""

    business = Business(
        name=data.business_name.strip(),
        phone=phone_full,
        city=data.city.strip(),
        is_whatsapp=data.is_whatsapp,
        onboarding_status="pending",
    )
    session.add(business)
    session.flush()

    user = User(
        business_id=business.id,
        name=data.name.strip(),
        email=email,
        phone=phone_full or None,
        role=UserRole.OWNER,
        is_active=True,
    )
    session.add(user)
    session.flush()

    if hasattr(business, "owner_user_id"):
        business.owner_user_id = user.id
        session.add(business)

    identity = AuthIdentity(
        user_id=user.id,
        provider=AuthProvider.EMAIL,
        provider_id=email,
        password_hash=hash_password(data.password),
        provider_email=email,
        verified=False,
    )
    session.add(identity)
    session.commit()
    session.refresh(user)
    session.refresh(business)

    # Pipeline is NOT created here — it's created during onboarding
    # based on the user's selected persona (business_type).

    tokens = _issue_tokens(session, user, device_info)
    return _build_auth_response(user, business, tokens)


def login(
    session: Session,
    data: LoginRequest,
    device_info: str | None = None,
) -> AuthResponse:
    """
    Authenticate with email + password.
    """
    email = data.email.strip().lower()

    identity = get_identity_by_provider(session, AuthProvider.EMAIL, email)
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not identity.password_hash or not verify_password(data.password, identity.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    user = get_user_by_id(session, identity.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Please contact support.",
        )

    user.last_login_at = datetime.now(timezone.utc)
    session.add(user)
    session.commit()
    session.refresh(user)

    business = get_business_by_id(session, user.business_id)
    if not business:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Business not found for user",
        )

    tokens = _issue_tokens(session, user, device_info)
    return _build_auth_response(user, business, tokens)


def refresh_access_token(
    session: Session,
    raw_refresh_token: str,
    device_info: str | None = None,
) -> dict[str, str]:
    """
    Exchange a refresh token for a new access token + new refresh token.
    """
    token_hash = hash_refresh_token(raw_refresh_token)
    stored_token = get_refresh_token_by_hash(session, token_hash)

    if not stored_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if stored_token.is_revoked:
        revoke_all_user_tokens(session, stored_token.user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked. All sessions have been terminated for security. Please log in again.",
        )

    if stored_token.expires_at < datetime.now(timezone.utc):
        revoke_refresh_token(session, stored_token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired. Please log in again.",
        )

    revoke_refresh_token(session, stored_token)

    user = get_user_by_id(session, stored_token.user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found or deactivated",
        )

    tokens = _issue_tokens(session, user, device_info)
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "token_type": "bearer",
    }


def logout(
    session: Session,
    raw_refresh_token: str,
) -> None:
    """
    Logout by revoking the refresh token.
    """
    token_hash = hash_refresh_token(raw_refresh_token)
    stored_token = get_refresh_token_by_hash(session, token_hash)

    if stored_token and not stored_token.is_revoked:
        revoke_refresh_token(session, stored_token)


def logout_all_devices(
    session: Session,
    user_id: uuid.UUID,
) -> int:
    """Revoke all refresh tokens for a user."""
    return revoke_all_user_tokens(session, user_id)
