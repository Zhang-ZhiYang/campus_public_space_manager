# bookings/apps.py
from django.apps import AppConfig

class BookingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bookings'

    def ready(self):
        # 局部导入以避免循环引用和AppRegistryNotReady错误
        import bookings.signals
        from core.dao import DAOFactory
        from bookings.dao.booking_dao import BookingDAO
        from bookings.dao.violation_dao import ViolationDAO
        from bookings.dao.penalty_dao import UserPenaltyPointsPerSpaceTypeDAO
        from bookings.dao.ban_policy_dao import SpaceTypeBanPolicyDAO
        from bookings.dao.user_ban_dao import UserSpaceTypeBanDAO
        from bookings.dao.exemption_dao import UserSpaceTypeExemptionDAO

        from core.service import ServiceFactory
        from bookings.service.booking_service import BookingService
        from bookings.service.violation_service import ViolationService
        from bookings.service.user_management_service import UserManagementService

        # 注册 DAOs
        DAOFactory.register_dao('booking', BookingDAO)
        DAOFactory.register_dao('violation', ViolationDAO)
        DAOFactory.register_dao('penalty_points', UserPenaltyPointsPerSpaceTypeDAO)
        DAOFactory.register_dao('ban_policy', SpaceTypeBanPolicyDAO)
        DAOFactory.register_dao('user_ban', UserSpaceTypeBanDAO)
        DAOFactory.register_dao('exemption', UserSpaceTypeExemptionDAO)

        # 注册 Services
        ServiceFactory.register_service(BookingService)
        ServiceFactory.register_service(ViolationService)
        ServiceFactory.register_service(UserManagementService)