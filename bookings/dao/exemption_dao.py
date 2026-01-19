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
        """
        # 豁免记录的 active 逻辑: start_date 为空或早于当前时间，并且 end_date 为空或晚于当前时间

        # 将所有查询条件合并到 Q 对象中，避免“位置参数在关键字参数之后”的错误
        query_conditions = (
                Q(user=user) &
                Q(space_type=space_type) &  # 查找特定空间类型，若为None则查找全局
                (Q(start_date__isnull=True) | Q(start_date__lte=timezone.now())) &
                (Q(end_date__isnull=True) | Q(end_date__gt=timezone.now()))
        )

        return self.get_queryset().filter(query_conditions).order_by('-granted_at').first()

    def get_user_exemption_by_id(self, exemption_id: int) -> Optional[UserSpaceTypeExemption]:
        """根据ID获取单个用户豁免记录。"""
        try:
            return self.get_queryset().get(pk=exemption_id)
        except self.model.DoesNotExist:
            return None