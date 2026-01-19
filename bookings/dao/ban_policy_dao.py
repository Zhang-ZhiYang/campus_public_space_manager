# bookings/dao/ban_policy_dao.py
from core.dao import BaseDAO
from bookings.models import SpaceTypeBanPolicy
from spaces.models import SpaceType
from django.db.models import QuerySet, Q
from typing import Optional

class SpaceTypeBanPolicyDAO(BaseDAO):
    """
    SpaceTypeBanPolicy 模型的数据访问对象。
    """
    model = SpaceTypeBanPolicy

    def get_queryset(self) -> QuerySet[SpaceTypeBanPolicy]:
        """
        获取基础 QuerySet，预加载 space_type。
        """
        return super().get_queryset().select_related('space_type')

    def get_applicable_policies(
            self, space_type: Optional[SpaceType], current_points: int
    ) -> QuerySet[SpaceTypeBanPolicy]:
        """
        获取适用于给定空间类型和当前点数的所有活跃禁用策略。
        按阈值点数（高到低）和优先级（高到低）排序。
        """
        query = Q(is_active=True, threshold_points__lte=current_points)

        if space_type:
            # 适用于特定空间类型或全局策略
            query &= (Q(space_type=space_type) | Q(space_type__isnull=True))
        else:
            # 如果没有指定 space_type，则只查找全局策略
            query &= Q(space_type__isnull=True)

        return self.get_queryset().filter(query).order_by('-priority', '-threshold_points')

    def get_ban_policy_by_id(self, policy_id: int) -> Optional[SpaceTypeBanPolicy]:
        """根据ID获取单个禁用策略。"""
        try:
            return self.get_queryset().get(pk=policy_id)
        except self.model.DoesNotExist:
            return None