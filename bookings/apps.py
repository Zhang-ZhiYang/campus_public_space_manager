# bookings/apps.py
from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class BookingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bookings'
    verbose_name = "预订管理"

    def ready(self):
        # 1. 导入信号，确保信号处理器被连接 (如果你保留 signals.py)
        # import bookings.signals
        # logger.info("Bookings signals loaded.")

        # 2. 导入 DAOFactory & ServiceFactory
        from core.dao.factory import DAOFactory
        from core.service.factory import ServiceFactory # <-- 确保从 core.service.factory 导入

        # 3. 导入所有 DAO 类
        from bookings.dao.booking_dao import BookingDAO
        from bookings.dao.violation_dao import ViolationDAO
        from bookings.dao.penalty_dao import UserPenaltyPointsPerSpaceTypeDAO
        from bookings.dao.ban_policy_dao import SpaceTypeBanPolicyDAO
        from bookings.dao.user_ban_dao import UserSpaceTypeBanDAO
        from bookings.dao.exemption_dao import UserSpaceTypeExemptionDAO
        from bookings.dao.daily_booking_limit_dao import DailyBookingLimitDAO

        # 4. 导入所有 Service 类
        from bookings.service.booking_service import BookingService
        from bookings.service.base_booking_validation_service import BaseBookingValidationService
        from bookings.service.daily_booking_limit_service import DailyBookingLimitService
        from bookings.service.user_ban_service import UserBanService
        from bookings.service.user_exemption_service import UserExemptionService
        from bookings.service.ban_policy_service import BanPolicyService
        from bookings.service.penalty_service import PenaltyService
        from bookings.service.violation_service import ViolationService

        # 5. 注册所有 DAO
        DAOFactory.register_dao('booking', BookingDAO)
        DAOFactory.register_dao('violation', ViolationDAO)
        DAOFactory.register_dao('user_penalty_points_per_space_type', UserPenaltyPointsPerSpaceTypeDAO)
        DAOFactory.register_dao('space_type_ban_policy', SpaceTypeBanPolicyDAO)
        DAOFactory.register_dao('user_space_type_ban', UserSpaceTypeBanDAO)
        DAOFactory.register_dao('user_space_type_exemption', UserSpaceTypeExemptionDAO)
        DAOFactory.register_dao('daily_booking_limit', DailyBookingLimitDAO)
        logger.info("Bookings DAOs registered with DAOFactory.")

        # 6. 注册所有 Service (使用 ServiceClass.__name__ 作为 key)
        ServiceFactory.register_service(BookingService)
        ServiceFactory.register_service(BaseBookingValidationService)
        ServiceFactory.register_service(DailyBookingLimitService)
        ServiceFactory.register_service(UserBanService)
        ServiceFactory.register_service(UserExemptionService)
        ServiceFactory.register_service(BanPolicyService)
        ServiceFactory.register_service(PenaltyService)
        ServiceFactory.register_service(ViolationService)
        logger.info("Bookings Services registered with ServiceFactory.")