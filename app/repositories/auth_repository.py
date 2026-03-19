"""
Auth repository - data access for auth_identities and refresh_tokens.
"""

import uuid

from sqlmodel import Session, select

from app.models.auth_identity import AuthIdentity
from app.models.enums import AuthProvider
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
