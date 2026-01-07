# bookings/apps.py
from django.apps import AppConfig

class BookingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bookings'

    def ready(self):
        # 1. 导入信号（信号通常在这里导入）
        import bookings.signals

        # 2. 导入 DAOFactory 和 ServiceFactory
        from core.dao import DAOFactory
        from core.service import ServiceFactory

        # 3. 在 ready() 方法内部导入所有 DAO 类。
        #    这确保了 DAO 模块及其内部的模型导入仅在应用注册表准备就绪之后才发生。
        from bookings.dao.booking_dao import BookingDAO
        from bookings.dao.violation_dao import ViolationDAO
        from bookings.dao.penalty_dao import UserPenaltyPointsPerSpaceTypeDAO
        from bookings.dao.ban_policy_dao import SpaceTypeBanPolicyDAO
        from bookings.dao.user_ban_dao import UserSpaceTypeBanDAO
        from bookings.dao.exemption_dao import UserSpaceTypeExemptionDAO
        from bookings.dao.daily_booking_limit_dao import DailyBookingLimitDAO

        # 4. 导入所有 Service 类
        from bookings.service.booking_service import BookingService
        from bookings.service.violation_service import ViolationService
        from bookings.service.user_management_service import UserManagementService

        # 5. 使用 DAOFactory 注册所有的 DAO。键名应与 Service 中的 _dao_map 保持一致。
        DAOFactory.register_dao('booking', BookingDAO)
        DAOFactory.register_dao('violation', ViolationDAO)
        DAOFactory.register_dao('penalty_points', UserPenaltyPointsPerSpaceTypeDAO) # 确保键名一致
        DAOFactory.register_dao('ban_policy', SpaceTypeBanPolicyDAO)
        DAOFactory.register_dao('user_ban', UserSpaceTypeBanDAO)
        DAOFactory.register_dao('exemption', UserSpaceTypeExemptionDAO)
        DAOFactory.register_dao('daily_booking_limit', DailyBookingLimitDAO) # 新增注册

        # 6. 使用 ServiceFactory 注册所有的 Service
        ServiceFactory.register_service(BookingService)
        ServiceFactory.register_service(ViolationService)
        ServiceFactory.register_service(UserManagementService)