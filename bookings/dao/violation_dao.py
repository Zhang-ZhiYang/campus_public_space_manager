# bookings/dao/violation_dao.py
from django.db.models import QuerySet, Q
from django.conf import settings
from guardian.shortcuts import get_objects_for_user
from typing import Optional, Dict, Any, Tuple

from bookings.models import Violation, Booking, UserPenaltyPointsPerSpaceType  # 导入 Booking 来协助 Violation 模型的创建
from core.dao import BaseDAO  # 导入 BaseDAO
from spaces.models import Space, SpaceType
from users.models import CustomUser  # 明确导入 CustomUser


class ViolationDAO(BaseDAO):
    model = Violation

    def get_queryset(self) -> QuerySet[Violation]:
        """
        获取基础 QuerySet，并预加载常用关联对象以优化查询。
        """
        return super().get_queryset().select_related(
            'user', 'booking__space', 'booking__bookable_amenity__space',
            'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type'
        )

    def get_violations_for_admin_view(self, user: CustomUser) -> QuerySet[Violation]:
        """
        根据用户权限获取适用于 Admin 视图的违规记录 QuerySet。
        超级管理员和系统管理员可以看到所有，空间管理员看到其管理空间类型下的违规。
        """
        qs = self.get_queryset()

        if user.is_superuser or user.is_system_admin:
            return qs

        # 获取用户有管理权限的空间实例
        managed_spaces = get_objects_for_user(
            user, 'spaces.can_manage_space_details', klass=Space
        )

        # 提取这些空间对应的空间类型ID
        # 过滤掉 None 确保只有有效的 space_type_id
        managed_spacetype_ids = [
            space_type_id for space_type_id in
            managed_spaces.values_list('space_type__id', flat=True).distinct()
            if space_type_id is not None
        ]

        # 过滤违规记录：
        # 1. 违规记录直接关联的空间类型在用户管理的类型中
        # 2. 违规记录的预订目标（空间或设施所在空间）的空间类型在用户管理的类型中
        # 使用 Q 对象进行 OR 查询，并使用 distinct() 避免重复
        return qs.filter(
            Q(space_type__id__in=managed_spacetype_ids) |
            Q(booking__space__space_type__id__in=managed_spacetype_ids) |
            Q(booking__bookable_amenity__space__space_type__id__in=managed_spacetype_ids)
        ).distinct()

    def get_violation_by_id(self, violation_id: int) -> Optional[Violation]:
        """根据ID获取单个违规记录。"""
        try:
            return self.get_queryset().get(pk=violation_id)
        except Violation.DoesNotExist:
            return None

    def create_violation(self, user: CustomUser, violation_type: str, description: str,
                         penalty_points: int = 1, booking: Optional[Booking] = None,
                         space_type: Optional[SpaceType] = None, issued_by: Optional[CustomUser] = None) -> Violation:
        """
        创建新的违规记录。
        """
        # 如果 issued_by 未提供，通常默认为执行此操作的 user
        if issued_by is None:
            issued_by = user

        # 注意：这里调用的是 BaseDAO 的 create 方法，它应该会调用 model.save()
        # 从而触发 pre_save 和 post_save 信号。
        return self.create(  # BaseDAO 继承了 model.objects.create 的行为
            user=user,
            booking=booking,
            space_type=space_type,
            violation_type=violation_type,
            description=description,
            issued_by=issued_by,
            penalty_points=penalty_points
        )

    def update_violation(self, violation_instance: Violation, **kwargs) -> Violation:
        """
        更新现有的违规记录实例。
        这个方法应确保调用实例的 save()，以便触发表单校验和模型信号。
        """
        for attr, value in kwargs.items():
            setattr(violation_instance, attr, value)
        violation_instance.full_clean()  # 强制执行模型 clean 方法
        violation_instance.save()  # 触发信号
        return violation_instance

    def delete_violation(self, violation_instance: Violation) -> None:
        """
        删除指定的违规记录实例。
        """
        violation_instance.delete()  # 触发信号

    def get_user_penalty_points_record(self, user: CustomUser, space_type: Optional[SpaceType]) -> Optional[
        UserPenaltyPointsPerSpaceType]:
        """
        获取用户在特定空间类型下的活跃违约点数记录。
        """
        try:
            return UserPenaltyPointsPerSpaceType.objects.get(user=user, space_type=space_type)
        except UserPenaltyPointsPerSpaceType.DoesNotExist:
            return None

    def get_or_create_user_penalty_points_record(self, user: CustomUser, space_type: Optional[SpaceType]) -> Tuple[
        UserPenaltyPointsPerSpaceType, bool]:
        """
        获取或创建用户在特定空间类型下的活跃违约点数记录。
        """
        return UserPenaltyPointsPerSpaceType.objects.get_or_create(user=user, space_type=space_type)

    def get_managed_spacetypes_by_user(self, user: CustomUser) -> QuerySet[SpaceType]:
        """
        获取用户有管理权限的空间类型列表。
        """
        if user.is_superuser or user.is_system_admin:
            return SpaceType.objects.all()

        managed_spaces = get_objects_for_user(user, 'spaces.can_manage_space_details', klass=Space)
        # 从 managed_spaces 中提取 space_type_id，并过滤掉 None
        return SpaceType.objects.filter(
            id__in=[
                sid for sid in managed_spaces.values_list('space_type__id', flat=True).distinct()
                if sid is not None
            ]
        )

    # 可以根据需要添加更多查询方法，例如：
    # def get_active_bans_for_user(self, user: CustomUser) -> QuerySet[UserSpaceTypeBan]:
    #     """获取用户当前活跃的所有禁用记录。"""
    #     from bookings.models import UserSpaceTypeBan # 延迟导入以避免循环依赖
    #     return UserSpaceTypeBan.objects.filter(
    #         user=user,
    #         end_date__gt=timezone.now()
    #     )