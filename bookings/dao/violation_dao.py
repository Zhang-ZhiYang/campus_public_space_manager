# bookings/dao/violation_dao.py
from django.db.models import QuerySet, Q
from django.conf import settings
from guardian.shortcuts import get_objects_for_user
from typing import Optional, Dict, Any, Tuple

from bookings.models import Violation, Booking, UserPenaltyPointsPerSpaceType
from core.dao import BaseDAO
from spaces.models import Space, SpaceType
from users.models import CustomUser
from django.utils import timezone  # NEW: 导入 timezone


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
        视图层确保用户已认证并通过角色检查，Service层负责根据对象级权限过滤数据。
        """
        qs = self.get_queryset()

        if user.is_superuser or user.is_system_admin:
            return qs

        # 获取用户有管理权限的空间实例 (基于 guardian)
        managed_spaces = get_objects_for_user(
            user, 'spaces.can_manage_space_details', klass=Space
        )

        # 提取这些空间对应的空间类型ID
        managed_spacetype_ids = [
            space_type_id for space_type_id in
            managed_spaces.values_list('space_type__id', flat=True).distinct()
            if space_type_id is not None
        ]

        # 过滤违规记录：
        # 1. 违规记录直接关联的空间类型在用户管理的类型中
        # 2. 违规记录的预订目标（空间或设施所在空间）的空间类型在用户管理的类型中
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
        if issued_by is None:
            issued_by = user

        instance = self.model(
            user=user,
            booking=booking,
            space_type=space_type,
            violation_type=violation_type,
            description=description,
            issued_by=issued_by,
            penalty_points=penalty_points,
            is_resolved=False,  # 确保新创建的违规默认为未解决
            resolved_at=None,
            resolved_by=None
        )
        instance.full_clean()  # 强制执行模型验证
        instance.save()  # 调用 save 方法触发信号
        return instance

    def update_violation(self, violation_instance: Violation, **kwargs) -> Violation:
        """
        更新现有的违规记录实例。
        这个方法应确保调用实例的 save()，以便触发表单校验和模型信号。
        """
        for attr, value in kwargs.items():
            setattr(violation_instance, attr, value)
        violation_instance.full_clean()
        violation_instance.save()  # 调用 save 方法触发信号
        return violation_instance

    # NEW: 新增更新解决状态的方法
    def update_violation_status(self, violation_id: int, is_resolved: bool,
                                resolved_by: Optional[CustomUser] = None) -> Optional[Violation]:
        """
        更新违规记录的解决状态。
        此方法将获取实例，更新字段，然后调用 `save()` 来触发信号，确保点数和禁用状态的更新。
        """
        violation_instance = self.get_violation_by_id(violation_id)
        if not violation_instance:
            return None

        # 存储旧状态，以便信号能使用
        violation_instance._old_is_resolved = violation_instance.is_resolved
        violation_instance._old_penalty_points = violation_instance.penalty_points
        # 必须存储旧的空间类型，因为修改违规记录的关联空间类型会影响不同统计维度
        violation_instance._old_cached_space_type_for_penalty_calc = violation_instance.space_type

        violation_instance.is_resolved = is_resolved
        if is_resolved:
            violation_instance.resolved_at = timezone.now()
            violation_instance.resolved_by = resolved_by
        else:
            violation_instance.resolved_at = None
            violation_instance.resolved_by = None

        # 调用 save() 触发 signals
        violation_instance.save(update_fields=['is_resolved', 'resolved_at', 'resolved_by', 'updated_at'])
        return violation_instance

    def delete_violation(self, violation_instance: Violation) -> None:
        """
        删除指定的违规记录实例。
        """
        violation_instance.delete()  # 调用 delete 方法触发信号

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
        return SpaceType.objects.filter(
            id__in=[
                sid for sid in managed_spaces.values_list('space_type__id', flat=True).distinct()
                if sid is not None
            ]
        )