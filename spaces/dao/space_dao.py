# spaces/dao/space_dao.py
from django.db.models import QuerySet, Q
from django.conf import settings
from guardian.shortcuts import get_objects_for_user
from typing import List, Tuple

from spaces.models import Space, SpaceType, BookableAmenity
from bookings.models import Booking # 跨应用导入
from core.dao import BaseDAO # 导入 BaseDAO

CustomUser = settings.AUTH_USER_MODEL

class SpaceDAO(BaseDAO):
    model = Space

    def get_queryset(self) -> QuerySet[Space]:
        return super().get_queryset().select_related('space_type', 'parent_space', 'managed_by')

    def get_spaces_for_admin_view(self, user: CustomUser, base_queryset: QuerySet[Space]) -> QuerySet[Space]:
        if user.is_superuser or user.is_system_admin:
            return base_queryset
        return get_objects_for_user(user, 'spaces.can_manage_space_details', klass=base_queryset)

    def space_has_children(self, space: Space) -> bool:
        return space.child_spaces.exists()

    def space_has_bookings(self, space: Space, bookings_loaded: bool) -> bool:
        if not bookings_loaded:
            return False
        return Booking.objects.filter(space=space).exists()

    def space_amenities_have_bookings(self, space: Space, bookings_loaded: bool) -> bool:
        if not bookings_loaded:
            return False
        return space.bookable_amenities.filter(booking__isnull=False).exists() # 假设BookableAmenity有反向关系名为booking

    # create_space, update_space, delete_spaces 现在可以简化或删除，因为 BaseDAO 已经提供了通用方法
    # 如果有特定逻辑，比如设置 default values，可以保留
    def create_space(self, name: str, location: str, space_type: SpaceType, managed_by: CustomUser = None, **kwargs) -> Space:
        return self.create(name=name, location=location, space_type=space_type, managed_by=managed_by, **kwargs)

    def update_space(self, space: Space, **kwargs) -> Space:
        return self.update(space, **kwargs)

    def delete_spaces_by_ids(self, space_ids: List[int]) -> Tuple[int, dict]: # 与BaseDAO的delete不同，这是批量通过ID删除
        return self.filter(id__in=space_ids).delete()

class BookableAmenityDAO(BaseDAO): # 为 BookableAmenity 创建一个专门的 DAO
    model = BookableAmenity

    def get_queryset(self) -> QuerySet[BookableAmenity]:
        return super().get_queryset().select_related('amenity', 'space__space_type')

    def get_bookable_amenities_for_space(self, space: Space, user: CustomUser) -> QuerySet[BookableAmenity]:
        qs = self.filter(space=space)
        if user.is_superuser or user.is_system_admin:
            return qs.select_related('amenity')
        # Here, we assume the permission check for the parent space is handled upstream.
        # This DAO just filters by space.
        return qs