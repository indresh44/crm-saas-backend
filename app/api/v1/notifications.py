from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.notification import NotificationRead
from app.models.user import User
from app.services.notification_service import (
    list_notifications as service_list_notifications,
    mark_notification_read as service_mark_notification_read,
)

router = APIRouter()


@router.get("/notifications", response_model=List[NotificationRead])
def list_notifications(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[NotificationRead]:
    notifications = service_list_notifications(session=session, current_user=current_user)
    return notifications


@router.patch("/notifications/{notification_id}", response_model=NotificationRead)
def update_notification(
    notification_id: UUID,
    is_read: bool,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> NotificationRead:
    notification = service_mark_notification_read(
        session=session,
        current_user=current_user,
        notification_id=notification_id,
        is_read=is_read,
    )
    return notification

