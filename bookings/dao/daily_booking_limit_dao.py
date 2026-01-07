# bookings/dao/daily_booking_limit_dao.py
from core.dao import BaseDAO
from bookings.models import DailyBookingLimit
from django.db.models import QuerySet
from typing import Optional, List


class DailyBookingLimitDAO(BaseDAO):
    """
    DailyBookingLimit 模型的数据访问对象。
    """
    model = DailyBookingLimit

    def get_active_limit_for_group(self, group_id: int) -> Optional[DailyBookingLimit]:
        """
        根据组ID获取该组的活跃每日预订限制。
        """
        try:
            return self.get_queryset().filter(group_id=group_id, is_active=True).first()
        except DailyBookingLimit.DoesNotExist:
            return None

    def get_all_active_limits(self) -> QuerySet[DailyBookingLimit]:
        """
        获取所有活跃的每日预订限制规则。
        """
        return self.get_queryset().filter(is_active=True)

    def get_active_limits_for_group_ids(self, group_ids: List[int]) -> QuerySet[DailyBookingLimit]:
        """
        获取指定ID的用户组中，所有活跃且有具体限制的每日预订限制规则。
        按限制数升序排列，这样更容易找到最严格的。
        """
        return self.get_queryset().filter(
            group_id__in=group_ids,
            is_active=True,
            max_bookings__gt=0 # 只考虑有实际限制的规则 (>0 表示有实际天数限制)
        ).order_by('max_bookings')