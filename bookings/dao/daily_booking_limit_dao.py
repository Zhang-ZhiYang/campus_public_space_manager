# bookings/dao/daily_booking_limit_dao.py
from core.dao import BaseDAO
from bookings.models import DailyBookingLimit
from spaces.models import SpaceType # 确保导入 SpaceType
from django.db.models import QuerySet, Q
from typing import Optional, List, Tuple

class DailyBookingLimitDAO(BaseDAO):
    """
    DailyBookingLimit 模型的数据访问对象。
    """
    model = DailyBookingLimit

    def get_queryset(self) -> QuerySet[DailyBookingLimit]:
        """
        获取基础 QuerySet，预加载 group 和 space_type。
        """
        return super().get_queryset().select_related('group', 'space_type')

    def get_active_limit_for_group(self, group_id: int) -> Optional[DailyBookingLimit]:
        """
        根据组ID获取该组的（全局）活跃每日预订限制。
        仅获取 space_type 为 None 的全局限制。
        """
        try:
            return self.get_queryset().filter(group_id=group_id, is_active=True, space_type__isnull=True).first()
        except self.model.DoesNotExist:
            return None

    def get_all_active_limits(self) -> QuerySet[DailyBookingLimit]:
        """
        获取所有活跃的每日预订限制规则。
        """
        return self.get_queryset().filter(is_active=True)

    def get_applicable_limits_for_groups_and_spacetype(
            self, group_ids: List[int], space_type: Optional[SpaceType] = None
    ) -> QuerySet[DailyBookingLimit]:
        """
        获取指定用户组和/或指定空间类型的所有活跃且有具体限制的每日预订限制规则。
        结果按优先级（高到低）和最大预订次数（低到高）排序。
        这样 service 层可以轻松找到最严格或最适用的规则。
        """
        query = Q(group_id__in=group_ids, is_active=True, max_bookings__gt=0)

        # 考虑特定空间类型或全局限制
        if space_type:
            query &= (Q(space_type=space_type) | Q(space_type__isnull=True))
        else:
            # 如果没有指定 space_type，则只查找全局限制
            query &= Q(space_type__isnull=True)

        return self.get_queryset().filter(query).order_by('-priority', 'max_bookings')

    def get_limit_by_group_and_spacetype(
            self, group_id: int, space_type_id: Optional[int]
    ) -> Optional[DailyBookingLimit]:
        """
        获取特定用户组和空间类型（或全局）的每日预订限制。
        当 space_type_id 为 None 时，查找全局限制。
        """
        try:
            if space_type_id is None:
                return self.get_queryset().get(group_id=group_id, space_type__isnull=True)
            else:
                return self.get_queryset().get(group_id=group_id, space_type_id=space_type_id)
        except self.model.DoesNotExist:
            return None