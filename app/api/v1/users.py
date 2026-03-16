from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.models.user import UserCreate, UserRead
from app.services.user_service import (
    create_user as service_create_user,
    get_user as service_get_user,
    list_users as service_list_users,
)

router = APIRouter()


@router.post("/users", response_model=UserRead)
def create_user(
    payload: UserCreate,
    session: Session = Depends(get_session),
) -> UserRead:
    user = service_create_user(session=session, data=payload)
    return user


@router.get("/users", response_model=List[UserRead])
def list_users(
    session: Session = Depends(get_session),
) -> List[UserRead]:
    users = service_list_users(session=session)
    return users


@router.get("/users/{id}", response_model=UserRead)
def get_user(
    id: UUID,
    session: Session = Depends(get_session),
) -> UserRead:
    user = service_get_user(session=session, user_id=id)
    return user
