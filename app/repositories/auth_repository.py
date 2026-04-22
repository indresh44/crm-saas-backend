"""
Auth repository - data access for auth_identities, refresh_tokens,
and password_reset_tokens.
"""

import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models.auth_identity import AuthIdentity
from app.models.enums import AuthProvider
from app.models.password_reset_token import PasswordResetToken
from app.models.refresh_token import RefreshToken


def create_auth_identity(session: Session, identity: AuthIdentity) -> AuthIdentity:
    """Create a new auth identity record."""
    session.add(identity)
    session.commit()
    session.refresh(identity)
    return identity


def get_identity_by_provider(
    session: Session,
    provider: AuthProvider,
    provider_id: str,
) -> AuthIdentity | None:
    """
    Lookup an auth identity by provider and provider_id.
    Used during login to find the user.
    """
    statement = select(AuthIdentity).where(
        AuthIdentity.provider == provider,
        AuthIdentity.provider_id == provider_id,
    )
    return session.exec(statement).first()


def get_identities_for_user(
    session: Session,
    user_id: uuid.UUID,
) -> list[AuthIdentity]:
    """Get all auth identities for a user."""
    statement = select(AuthIdentity).where(AuthIdentity.user_id == user_id)
    return list(session.exec(statement).all())


def create_refresh_token(session: Session, token: RefreshToken) -> RefreshToken:
    """Store a new refresh token."""
    session.add(token)
    session.commit()
    session.refresh(token)
    return token


def get_refresh_token_by_hash(
    session: Session,
    token_hash: str,
) -> RefreshToken | None:
    """Lookup a refresh token by its hash."""
    statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    return session.exec(statement).first()


def revoke_refresh_token(session: Session, token: RefreshToken) -> RefreshToken:
    """Revoke a single refresh token."""
    token.is_revoked = True
    session.add(token)
    session.commit()
    session.refresh(token)
    return token


def revoke_all_user_tokens(session: Session, user_id: uuid.UUID) -> int:
    """
    Revoke all refresh tokens for a user.
    Returns count of revoked tokens.
    """
    statement = select(RefreshToken).where(
        RefreshToken.user_id == user_id,
        RefreshToken.is_revoked == False,
    )
    tokens = session.exec(statement).all()
    count = 0
    for token in tokens:
        token.is_revoked = True
        session.add(token)
        count += 1
    session.commit()
    return count


def get_user_by_email(session: Session, email: str) -> "User | None":
    """
    Lookup a User via the EMAIL auth identity. Returns None if no such user exists.
    """
    from app.models.user import User

    statement = (
        select(User)
        .join(AuthIdentity, AuthIdentity.user_id == User.id)
        .where(
            AuthIdentity.provider == AuthProvider.EMAIL,
            AuthIdentity.provider_id == email,
        )
    )
    return session.exec(statement).first()


def get_recent_unused_reset_token(
    session: Session,
    user_id: uuid.UUID,
    cooldown_seconds: int,
) -> PasswordResetToken | None:
    """
    Return a recently-created, unused, non-expired reset token for this user
    (if one was issued within the last `cooldown_seconds`). Used as a simple
    per-email rate limiter — callers that find one should no-op silently to
    avoid resending spam and to avoid leaking timing info.
    """
    now = datetime.now(timezone.utc)
    statement = (
        select(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .order_by(PasswordResetToken.created_at.desc())
    )
    latest = session.exec(statement).first()
    if latest is None:
        return None
    age_seconds = (now - latest.created_at).total_seconds()
    if age_seconds < cooldown_seconds:
        return latest
    return None


def create_password_reset_token(
    session: Session,
    token: PasswordResetToken,
) -> PasswordResetToken:
    """Store a new password reset token."""
    session.add(token)
    session.commit()
    session.refresh(token)
    return token


def get_password_reset_token_by_hash(
    session: Session,
    token_hash: str,
) -> PasswordResetToken | None:
    """Lookup a reset token by its hash. Does NOT check expiry/used state."""
    statement = select(PasswordResetToken).where(
        PasswordResetToken.token_hash == token_hash
    )
    return session.exec(statement).first()


def mark_password_reset_token_used(
    session: Session,
    token: PasswordResetToken,
) -> PasswordResetToken:
    """Mark the token as used. Commits."""
    token.used_at = datetime.now(timezone.utc)
    session.add(token)
    session.commit()
    session.refresh(token)
    return token


def get_email_identity_for_user(
    session: Session,
    user_id: uuid.UUID,
) -> AuthIdentity | None:
    """Return the EMAIL-provider AuthIdentity for this user, if any."""
    statement = select(AuthIdentity).where(
        AuthIdentity.user_id == user_id,
        AuthIdentity.provider == AuthProvider.EMAIL,
    )
    return session.exec(statement).first()
