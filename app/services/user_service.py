from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.enums import UserRole
from app.models.user import User, UserCreate
from app.repositories.business_repository import get_business_by_id, update_business
from app.repositories.user_repository import create_user as repo_create_user, get_user_by_id, list_users as repo_list_users


def create_user(session: Session, data: UserCreate) -> User:
    business = get_business_by_id(session, data.business_id)
    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found",
        )
    user = User(**data.model_dump())
    user = repo_create_user(session, user)
    if user.role == UserRole.OWNER:
        business.owner_user_id = user.id
        update_business(session, business)
    return user


def get_user(session: Session, user_id: UUID) -> User:
    user = get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


def list_users(session: Session) -> list[User]:
    return repo_list_users(session)
