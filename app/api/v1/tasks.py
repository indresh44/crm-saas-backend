from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.task import TaskCreate, TaskRead
from app.models.user import User
from app.services.task_service import (
    TaskUpdate,
    create_task as service_create_task,
    get_task as service_get_task,
    list_tasks as service_list_tasks,
    update_task as service_update_task,
)

router = APIRouter()


@router.post("/tasks", response_model=TaskRead)
def create_task(
    payload: TaskCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    task = service_create_task(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return task


@router.get("/tasks", response_model=List[TaskRead])
def list_tasks(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[TaskRead]:
    tasks = service_list_tasks(session=session, current_user=current_user)
    return tasks


@router.patch("/tasks/{task_id}", response_model=TaskRead)
def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    task = service_update_task(
        session=session,
        current_user=current_user,
        task_id=task_id,
        data=payload,
    )
    return task

