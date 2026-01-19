# bookings/apps.py (仅包含 ServiceFactory 注册相关部分)
from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class BookingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bookings'

    def ready(self):
        # ... (DAOFactory 注册部分保持不变)
        from core.dao import DAOFactory
        from core.service import ServiceFactory
        import bookings.signals

        from bookings.dao import (
            BookingDAO, DailyBookingLimitDAO, ViolationDAO,
            UserPenaltyPointsPerSpaceTypeDAO, SpaceTypeBanPolicyDAO,
            UserSpaceTypeBanDAO, UserSpaceTypeExemptionDAO,
        )

        # 导入所有 Service 类 (从 bookings.service.__init__.py 统一导入)
        from bookings.service import (
            DailyBookingLimitService,
            UserBanService,
            UserExemptionService,
            BookingPreliminaryService,  # 新增
            BookingValidationCreationService,  # 新增
        )

        DAOFactory.register_dao('booking', BookingDAO)
        DAOFactory.register_dao('daily_booking_limit', DailyBookingLimitDAO)
        DAOFactory.register_dao('violation', ViolationDAO)
        DAOFactory.register_dao('user_penalty_points', UserPenaltyPointsPerSpaceTypeDAO)
        DAOFactory.register_dao('space_type_ban_policy', SpaceTypeBanPolicyDAO)
        DAOFactory.register_dao('user_space_type_ban', UserSpaceTypeBanDAO)
        DAOFactory.register_dao('user_space_type_exemption', UserSpaceTypeExemptionDAO)
        logger.info("Bookings DAOs registered with DAOFactory.")

        # 使用 ServiceFactory 注册所有的 Service
        ServiceFactory.register_service(DailyBookingLimitService)
        ServiceFactory.register_service(UserBanService)
        ServiceFactory.register_service(UserExemptionService)
        ServiceFactory.register_service(BookingPreliminaryService)  # 新增
        ServiceFactory.register_service(BookingValidationCreationService)  # 新增
        logger.info("Bookings Services registered with ServiceFactory.")