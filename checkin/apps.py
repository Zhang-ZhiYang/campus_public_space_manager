# check_in/apps.py
from django.apps import AppConfig


class CheckInConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'check_in'
    verbose_name = '签到管理'

    def ready(self):
        # 导入 DAOFactory
        from core.dao import DAOFactory
        # 导入要注册的 DAO 类
        from .check_in_dao import CheckInRecordDAO

        # 使用 DAOFactory 注册 CheckInRecordDAO
        # 键名 'check_in_record' 应该与 Service 层中 _dao_map 的用名保持一致
        DAOFactory.register_dao('check_in_record', CheckInRecordDAO)

        # 这里不需要注册 Service，因为我们目前还没有 CheckInRecordService
        # 如果未来有 Service，则在这里导入并注册 ServiceFactory.register_service(...)

        # 导入信号 (如果 check_in 应用有自己的信号处理)
        # try:
        #     import check_in.signals
        # except ImportError:
        #     pass