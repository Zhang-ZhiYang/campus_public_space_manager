# bookings/apps.py
from django.apps import AppConfig
import logging

from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)

class BookingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bookings'

    def ready(self):
        from core.dao import DAOFactory
        from core.service import ServiceFactory
        import bookings.signals
        import bookings.signal_scheduling
        from bookings.dao import (
            BookingDAO, DailyBookingLimitDAO, ViolationDAO,
            UserPenaltyPointsPerSpaceTypeDAO, SpaceTypeBanPolicyDAO,
            UserSpaceTypeBanDAO, UserSpaceTypeExemptionDAO,
        )

        from bookings.service import (
            DailyBookingLimitService,
            UserBanService,
            UserExemptionService,
            BookingPreliminaryService,
            BookingValidationCreationService,
            BookingService,
            BookingStatusQueryService,
            ViolationService, # 注册 ViolationService
        )

        # 注册 DAOs
        DAOFactory.register_dao('booking', BookingDAO)
        DAOFactory.register_dao('daily_booking_limit', DailyBookingLimitDAO)
        DAOFactory.register_dao('violation', ViolationDAO) # 注册 violation_dao
        DAOFactory.register_dao('user_penalty_points', UserPenaltyPointsPerSpaceTypeDAO)
        DAOFactory.register_dao('space_type_ban_policy', SpaceTypeBanPolicyDAO)
        DAOFactory.register_dao('user_space_type_ban', UserSpaceTypeBanDAO)
        DAOFactory.register_dao('user_space_type_exemption', UserSpaceTypeExemptionDAO)
        logger.info("Bookings DAOs registered with DAOFactory.")

        # 注册 Services
        ServiceFactory.register_service(DailyBookingLimitService)
        ServiceFactory.register_service(UserBanService)
        ServiceFactory.register_service(UserExemptionService)
        ServiceFactory.register_service(BookingPreliminaryService)
        ServiceFactory.register_service(BookingValidationCreationService)
        ServiceFactory.register_service(BookingService)
        ServiceFactory.register_service(BookingStatusQueryService)
        ServiceFactory.register_service(ViolationService) # 注册 ViolationService
        logger.info("Bookings Services registered with ServiceFactory.")


        from celery import current_app
        from celery.schedules import crontab
        from bookings.tasks.violation_tasks import recalculate_all_penalty_points_and_apply_bans_task
        from bookings.tasks.no_show_tasks import create_no_show_violation_for_single_booking,process_overdue_approved_bookings_for_no_show
        from bookings.tasks.booking_tasks import reject_overdue_pending_bookings_task