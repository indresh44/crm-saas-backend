from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, SQLModel

from app.models.task import Task, TaskCreate
from app.models.user import User
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.task_repository import (
    create_task as repo_create_task,
    get_task_by_id,
    list_tasks_for_business,
    update_task as repo_update_task,
)


def create_task(
    session: Session,
    current_user: User,
    data: TaskCreate,
) -> Task:
    lead = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=data.lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead not found for current business",
        )

    task_data = data.model_dump()
    task_data["business_id"] = current_user.business_id
    task = Task(**task_data)
    return repo_create_task(session, task)


def get_task(
    session: Session,
    current_user: User,
    task_id: UUID,
) -> Task:
    task = get_task_by_id(
        session=session,
        business_id=current_user.business_id,
        task_id=task_id,
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )
    return task


def list_tasks(session: Session, current_user: User) -> List[Task]:
    return list_tasks_for_business(session, business_id=current_user.business_id)


class TaskUpdate(SQLModel):
    title: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[UUID] = None
    due_date: Optional[str] = None


def update_task(
    session: Session,
    current_user: User,
    task_id: UUID,
    data: TaskUpdate,
) -> Task:
    task = get_task(session, current_user, task_id)
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(task, field, value)
    return repo_update_task(session, task)

