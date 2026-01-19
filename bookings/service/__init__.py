# bookings/service/__init__.py
import logging

logger = logging.getLogger(__name__)

# 导入所有即将创建的 Service
from .daily_booking_limit_service import DailyBookingLimitService
from .user_ban_service import UserBanService
from .user_exemption_service import UserExemptionService
from .booking_preliminary_service import BookingPreliminaryService # 新增
from .booking_validation_creation_service import BookingValidationCreationService # 新增

# 所有的服务类列表
__all__ = [
    'DailyBookingLimitService',
    'UserBanService',
    'UserExemptionService',
    'BookingPreliminaryService', # 新增
    'BookingValidationCreationService', # 新增
    # 未来所有的 Service 都将在这里导入和导出
]

# (这里的注释保持原样，Service 的注册在 apps.py 中进行)