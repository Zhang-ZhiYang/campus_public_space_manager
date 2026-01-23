# checkin/apps.py
from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class CheckInConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'checkin'
    verbose_name = '签到管理'

    def ready(self):
        # 导入信号处理器，确保它们在应用加载时注册
        import checkin.signals
        from core.dao import DAOFactory
        from core.service import ServiceFactory
