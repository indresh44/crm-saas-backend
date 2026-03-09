from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.user import User


def get_current_user(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    session: Session = Depends(get_session),
) -> User:
    """
    Mock authentication dependency using X-User-Id header.
    """
    if x_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing",
        )

    try:
        user_id = UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid X-User-Id header",
        ) from exc

    statement = select(User).where(User.id == user_id)
    user = session.exec(statement).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user

