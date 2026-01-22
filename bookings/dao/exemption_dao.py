# bookings/dao/exemption_dao.py
from core.dao import BaseDAO
from bookings.models import UserSpaceTypeExemption, CustomUser
from spaces.models import SpaceType
from django.db.models import QuerySet, Q  # 确保导入 Q
from django.utils import timezone
from typing import Optional

class UserSpaceTypeExemptionDAO(BaseDAO):
    """
    UserSpaceTypeExemption 模型的数据访问对象。
    """
    model = UserSpaceTypeExemption

    def get_queryset(self) -> QuerySet[UserSpaceTypeExemption]:
        """
        获取基础 QuerySet，预加载 user、space_type 和 granted_by。
        """
        return super().get_queryset().select_related('user', 'space_type', 'granted_by')

    def get_active_exemption_for_user(
            self, user: CustomUser, space_type: Optional[SpaceType]
    ) -> Optional[UserSpaceTypeExemption]:
        """
        获取用户在特定空间类型下（或全局）当前活跃的豁免记录。
        同时会考虑针对特定空间类型和全局的豁免。
        """
        # 豁免记录的 active 逻辑: start_date 为空或早于当前时间，并且 end_date 为空或晚于当前时间

        # 初始过滤条件，不包含 space_type 相关的部分
        filter_conditions = (
                Q(user=user) &
                (Q(start_date__isnull=True) | Q(start_date__lte=timezone.now())) &
                (Q(end_date__isnull=True) | Q(end_date__gt=timezone.now()))
        )

        if space_type:
            # 如果指定了 space_type (例如，用户在预订“实验室”的空间)
            # 则查找：1. 针对该特定 space_type 的豁免，OR 2. 全局豁免 (space_type__isnull=True)
            filter_conditions &= (Q(space_type=space_type) | Q(space_type__isnull=True))
        else:
            # 如果未指定 space_type (例如，在某些不需要特定空间类型上下文的全局检查中)
            # 则只查找全局豁免 (space_type__isnull=True)
            filter_conditions &= Q(space_type__isnull=True)

        return self.get_queryset().filter(filter_conditions).order_by('-granted_at').first()

    def get_user_exemption_by_id(self, exemption_id: int) -> Optional[UserSpaceTypeExemption]:
        """根据ID获取单个用户豁免记录。"""
        try:
            return self.get_queryset().get(pk=exemption_id)
        except self.model.DoesNotExist:
            return None