# notifications/services.py
import logging

from core.service.base import BaseService
from core.service.service_result import ServiceResult
from users.models import CustomUser

# 导入 DAO 和 Task
from notifications.notification_dao import NotificationDAO
from notifications.tasks import send_email_task
from notifications.models import Notification

logger = logging.getLogger(__name__)


class NotificationService(BaseService):
    """
    通知服务：负责协调通知的创建和发送（邮件/系统消息）
    """
    _dao_map = {
        'notification_dao': 'notification'  # 在 ServiceFactory 初始化时会用到映射
    }

    def __init__(self):
        super().__init__()
        # 初始化 DAO，假设 BaseService 有 _get_dao_instance 或类似的机制
        # 如果没有自动注入，可以使用: self.notification_dao = NotificationDAO()
        self.notification_dao = NotificationDAO()

    def send_notification(self, user: CustomUser, title: str, content: str, message_type: str = 'SYSTEM') -> \
    ServiceResult[Notification]:
        """
        通用发送通知方法，供其他 Service 调用。

        :param user: 接收通知的用户对象 (CustomUser)
        :param title: 标题/邮件主题
        :param content: 内容
        :param message_type: 消息业务类型 (如 'BOOKING_SUCCESS', 'SYSTEM_ERROR')，可用于决定发送渠道
        :return: ServiceResult
        """
        try:
            if not user or not user.email:
                logger.warning(f"Skipping notification: User {user.pk if user else 'None'} has no email.")
                return ServiceResult.error_result("用户无邮箱，无法发送通知")

            # 默认逻辑：所有重要通知都发邮件
            # 这里将来可以扩展：根据 user 的设置决定是发邮件还是仅发站内信

            # 1. 通过 DAO 创建数据库记录
            notification = self.notification_dao.create_notification(
                recipient_email=user.email,
                subject=title,
                message=content,
                notification_type=Notification.NotificationType.EMAIL
            )

            logger.info(f"Notification record created (ID: {notification.id}) for User {user.pk} [{message_type}]")

            # 2. 触发 Celery 异步任务发送邮件
            send_email_task.delay(notification.id)

            return ServiceResult.success_result(data=notification, message="通知已加入发送队列")

        except Exception as e:
            logger.exception(f"Failed to send notification to User {user.pk if user else 'Unknown'}")
            # 通知发送失败通常不应阻断主业务流程（如预订），所以记录日志即可，返回错误结果
            return self._handle_exception(e, default_message="发送通知失败")

    # 保留旧的静态方法接口以兼容（如果有其他非 ServiceFactory 调用），或者可以选择删除
    @staticmethod
    def send_email_direct(recipient_email, subject, message):
        """兼容旧调用的简单接口"""
        dao = NotificationDAO()
        try:
            notification = dao.create_notification(recipient_email, subject, message)
            send_email_task.delay(notification.id)
            return notification
        except Exception as e:
            logger.error(f"Direct email send failed: {e}")
            raise e