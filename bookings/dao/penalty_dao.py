# bookings/dao/penalty_dao.py
from core.dao import BaseDAO
from bookings.models import UserPenaltyPointsPerSpaceType, CustomUser
from spaces.models import SpaceType
from django.db.models import QuerySet
from typing import Optional, Tuple

class UserPenaltyPointsPerSpaceTypeDAO(BaseDAO):
    """
    UserPenaltyPointsPerSpaceType 模型的数据访问对象。
    """
    model = UserPenaltyPointsPerSpaceType

    def get_queryset(self) -> QuerySet[UserPenaltyPointsPerSpaceType]:
        """
        获取基础 QuerySet，预加载 user 和 space_type。
        """
        return super().get_queryset().select_related('user', 'space_type')

    def get_user_penalty_points_record(
            self, user: CustomUser, space_type: Optional[SpaceType]
    ) -> Optional[UserPenaltyPointsPerSpaceType]:
        """
        获取用户在特定空间类型下的活跃违约点数记录。
        """
        try:
            return self.get_queryset().get(user=user, space_type=space_type)
        except self.model.DoesNotExist:
            return None

    def get_or_create_user_penalty_points_record(
            self, user: CustomUser, space_type: Optional[SpaceType]
    ) -> Tuple[UserPenaltyPointsPerSpaceType, bool]:
        """
        获取或创建用户在特定空间类型下的活跃违约点数记录。
        """
        return self.model.objects.get_or_create(user=user, space_type=space_type)

    def get_all_penalty_points_for_user(self, user: CustomUser) -> QuerySet[UserPenaltyPointsPerSpaceType]:
        """
        获取用户的所有违约点数记录（所有空间类型）。
        """
        return self.get_queryset().filter(user=user)