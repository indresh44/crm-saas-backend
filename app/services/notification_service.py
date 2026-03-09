from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.notification import Notification
from app.models.user import User
from app.repositories.notification_repository import (
    get_notification_by_id,
    list_notifications_for_user,
    update_notification as repo_update_notification,
)


def list_notifications(
    session: Session,
    current_user: User,
) -> List[Notification]:
    return list_notifications_for_user(session=session, user_id=current_user.id)


def mark_notification_read(
    session: Session,
    current_user: User,
    notification_id: UUID,
    is_read: bool,
) -> Notification:
    notification = get_notification_by_id(
        session=session,
        notification_id=notification_id,
        user_id=current_user.id,
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    notification.is_read = is_read
    return repo_update_notification(session, notification)

