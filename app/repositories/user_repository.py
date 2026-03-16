from uuid import UUID

from sqlalchemy import desc
from sqlmodel import Session, select

from app.models.user import User


def create_user(session: Session, user: User) -> User:
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def get_one_user_for_business(
    session: Session,
    business_id: UUID,
) -> User | None:
    """Return one user for the business (e.g. for webhook-created lead activities)."""
    statement = select(User).where(User.business_id == business_id).limit(1)
    return session.exec(statement).first()


def get_user_by_id(session: Session, user_id: UUID) -> User | None:
    statement = select(User).where(User.id == user_id)
    return session.exec(statement).first()


def list_users(session: Session) -> list[User]:
    statement = select(User).order_by(desc(User.created_at))
    return list(session.exec(statement).all())
