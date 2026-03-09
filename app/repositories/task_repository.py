from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.task import Task


def create_task(session: Session, task: Task) -> Task:
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def get_task_by_id(
    session: Session,
    business_id: UUID,
    task_id: UUID,
) -> Optional[Task]:
    statement = select(Task).where(
        Task.id == task_id,
        Task.business_id == business_id,
    )
    return session.exec(statement).first()


def list_tasks_for_business(session: Session, business_id: UUID) -> List[Task]:
    statement = select(Task).where(Task.business_id == business_id)
    return list(session.exec(statement).all())


def update_task(session: Session, task: Task) -> Task:
    session.add(task)
    session.commit()
    session.refresh(task)
    return task

