# bookings/dao/user_ban_dao.py
from core.dao import BaseDAO
from bookings.models import UserSpaceTypeBan, CustomUser
from spaces.models import SpaceType
from django.db.models import QuerySet
from django.utils import timezone
from typing import Optional

class UserSpaceTypeBanDAO(BaseDAO):
    """
    UserSpaceTypeBan 模型的数据访问对象。
    """
    model = UserSpaceTypeBan

    def get_queryset(self) -> QuerySet[UserSpaceTypeBan]:
        """
        获取基础 QuerySet，预加载 user、space_type 和 ban_policy_applied。
        """
        return super().get_queryset().select_related('user', 'space_type', 'ban_policy_applied')

    def get_active_ban_for_user(
            self, user: CustomUser, space_type: Optional[SpaceType]
    ) -> Optional[UserSpaceTypeBan]:
        """
        获取用户在特定空间类型下（或全局）当前活跃的禁用记录。
        """
        try:
            return self.get_queryset().filter(
                user=user,
                space_type=space_type, # 查找特定空间类型，若为None则查找全局
                end_date__gt=timezone.now() # 禁用结束时间晚于当前时间
            ).order_by('-issued_at').first() # 如果有多个活跃禁用，取最新的
        except self.model.DoesNotExist: # filter返回的是QuerySet，不会直接抛出DoesNotExist，但为了防御性保留
            return None

    def get_user_ban_by_id(self, ban_id: int) -> Optional[UserSpaceTypeBan]:
        """根据ID获取单个用户禁用记录。"""
        try:
            return self.get_queryset().get(pk=ban_id)
        except self.model.DoesNotExist:
            return None