# bookings/dao/violation_dao.py
from django.db.models import QuerySet, Q
from django.conf import settings
from guardian.shortcuts import get_objects_for_user

from bookings.models import Violation, Booking # 导入 Booking 来协助 Violation 模型的创建
from core.dao import BaseDAO # 导入 BaseDAO
from spaces.models import Space, SpaceType # 假设 Space 和 SpaceType 不需要 Mock 就可以导入

CustomUser = settings.AUTH_USER_MODEL

class ViolationDAO(BaseDAO):
    model = Violation

    def get_queryset(self) -> QuerySet[Violation]:
        return super().get_queryset().select_related(
            'user', 'booking__space', 'booking__bookable_amenity__space',
            'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type'
        )

    def get_violations_for_admin_view(self, user: CustomUser) -> QuerySet[Violation]:
        qs = self.get_queryset()

        if user.is_superuser or user.is_system_admin:
            return qs

        managed_spaces = get_objects_for_user(
            user, 'spaces.can_manage_space_details', klass=Space
        )
        managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
        managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

        return qs.filter(
            Q(space_type__id__in=managed_spacetype_ids) |
            Q(booking__space__space_type__id__in=managed_spacetype_ids) |
            Q(booking__bookable_amenity__space__space_type__id__in=managed_spacetype_ids)
        ).distinct()

    def create_violation(self, user: CustomUser, booking: Booking, space_type: SpaceType,
                         violation_type: str, description: str, penalty_points: int = 1):
        return self.create( # 调用 BaseDAO 的 create 方法
            user=user,
            booking=booking,
            space_type=space_type,
            violation_type=violation_type,
            description=description,
            issued_by=user,
            penalty_points=penalty_points
        )

    def get_managed_spacetypes_by_user(self, user: CustomUser) -> QuerySet[SpaceType]:
        if user.is_superuser or user.is_system_admin:
            return SpaceType.objects.all()

        managed_spaces = get_objects_for_user(user, 'spaces.can_manage_space_details', klass=Space)
        return SpaceType.objects.filter(id__in=managed_spaces.values_list('space_type__id', flat=True).distinct())