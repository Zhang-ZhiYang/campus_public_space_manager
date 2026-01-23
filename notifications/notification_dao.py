# notifications/dao/notification_dao.py
import logging
from typing import Optional

from core.dao import BaseDAO
from notifications.models import Notification

logger = logging.getLogger(__name__)


class NotificationDAO(BaseDAO):
    """
    负责 Notification 模型的数据访问对象
    """
    model = Notification  # <--- 在这里添加这一行！
    def create_notification(self, recipient_email: str, subject: str, message: str,
                            notification_type: str = Notification.NotificationType.EMAIL) -> Notification:
        """
        创建一条通知记录
        """
        try:
            notification = Notification.objects.create(
                recipient_email=recipient_email,
                subject=subject,
                message=message,
                notification_type=notification_type,
                status=Notification.Status.PENDING
            )
            return notification
        except Exception as e:
            logger.error(f"Error creating notification in DAO: {e}")
            raise e

    def get_notification_by_id(self, notification_id: int) -> Optional[Notification]:
        return Notification.objects.filter(id=notification_id).first()