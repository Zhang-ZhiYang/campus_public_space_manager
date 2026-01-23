# notifications/apps.py
from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifications'

    def ready(self):
        """
        App 启动时的初始化操作：注册 DAO 和 Services
        """
        # 延迟导入以避免 AppRegistryNotReady 错误
        from core.dao import DAOFactory
        from core.service import ServiceFactory

        # 导入你的 DAO 和 Service
        # 假设结构为 notifications/dao/notification_dao.py 和 notifications/services.py
        try:
            from notifications.notification_dao import NotificationDAO
            from notifications.services import NotificationService

            # 1. 注册 DAO
            # key 'notification' 用于在 Service 中 mapping: 'notification_dao': 'notification'
            DAOFactory.register_dao('notification', NotificationDAO)
            ServiceFactory.register_service(NotificationService)

        except ImportError as e:
            logger.error(f"Failed to import Notification components for registration: {e}")
            # 如果是开发早期阶段，可能允许忽略此错误，或者抛出异常中断启动
            # raise e