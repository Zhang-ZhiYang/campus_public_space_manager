# check_in/apps.py
from django.apps import AppConfig

class CheckInConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'check_in'
    verbose_name = '签到管理'

    def ready(self):
        # 导入 DAOFactory 和 ServiceFactory
        from core.dao import DAOFactory
        from core.service.factory import ServiceFactory # <--- 导入 ServiceFactory
        from check_in.tasks import check_in_tasks
        # 导入要注册的 DAO 类
        from .check_in_dao import CheckInRecordDAO
        # 导入要注册的 Service 类
        from .service.check_in_service import CheckInService # <--- 导入 CheckInService

        # 使用 DAOFactory 注册 CheckInRecordDAO
        DAOFactory.register_dao('check_in_record', CheckInRecordDAO)

        # 使用 ServiceFactory 注册 CheckInService
        ServiceFactory.register_service(CheckInService) # <--- 注册 CheckInService

        # 导入信号 (如果 check_in 应用有自己的信号处理)
        # try:
        #     import check_in.signals
        # except ImportError:
        #     pass