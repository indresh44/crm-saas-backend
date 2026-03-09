from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.notification import Notification


def list_notifications_for_user(
    session: Session,
    user_id: UUID,
) -> List[Notification]:
    statement = select(Notification).where(Notification.user_id == user_id)
    return list(session.exec(statement).all())


def get_notification_by_id(
    session: Session,
    notification_id: UUID,
    user_id: UUID,
) -> Optional[Notification]:
    statement = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == user_id,
    )
    return session.exec(statement).first()


def update_notification(session: Session, notification: Notification) -> Notification:
    session.add(notification)
    session.commit()
    session.refresh(notification)
    return notification

