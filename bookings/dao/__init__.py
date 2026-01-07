# bookings/dao/__init__.py

# 只导入并暴露 DAO 类。DAO_CLASSES 字典将在 bookings/apps.py 的 ready 方法中动态配置。
from .booking_dao import BookingDAO
from .violation_dao import ViolationDAO
from .penalty_dao import UserPenaltyPointsPerSpaceTypeDAO
from .ban_policy_dao import SpaceTypeBanPolicyDAO
from .user_ban_dao import UserSpaceTypeBanDAO
from .exemption_dao import UserSpaceTypeExemptionDAO
from .daily_booking_limit_dao import DailyBookingLimitDAO # 新增的每日预订限制 DAO

__all__ = [
    'BookingDAO',
    'ViolationDAO',
    'UserPenaltyPointsPerSpaceTypeDAO',
    'SpaceTypeBanPolicyDAO',
    'UserSpaceTypeBanDAO',
    'UserSpaceTypeExemptionDAO',
    'DailyBookingLimitDAO',
    # 不再在此处直接暴露 DAO_CLASSES，因为它被移动了
]