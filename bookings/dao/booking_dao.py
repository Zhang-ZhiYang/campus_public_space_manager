# bookings/dao/booking_dao.py
from django.db.models import Q, QuerySet
from django.conf import settings
from guardian.shortcuts import get_objects_for_user

from bookings.models import Booking
from core.dao import BaseDAO # 导入 BaseDAO

CustomUser = settings.AUTH_USER_MODEL

class BookingDAO(BaseDAO):
    model = Booking # 明确指定这个DAO操作的模型

    # __init__ 方法不再需要，会被 BaseDAO 自动处理

    def get_queryset(self) -> QuerySet[Booking]:
        """
        覆盖 BaseDAO 的方法，可以添加默认的 select_related/prefetch_related。
        """
        return super().get_queryset().select_related(
            'user', 'reviewed_by', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
        )

    def get_bookings_for_admin_view(self, user: CustomUser, spaces_loaded: bool) -> QuerySet[Booking]:
        qs = self.get_queryset() # 使用自身的 get_queryset 包含默认的 select_related

        if user.is_superuser or user.is_system_admin:
            return qs

        if not spaces_loaded:
            return qs.none()

        # 局部导入以避免潜在的循环依赖
        from spaces.models import Space, BookableAmenity

        managed_spaces_ids = get_objects_for_user(
            user, 'spaces.can_manage_space_bookings', klass=Space
        ).values_list('id', flat=True)

        managed_amenities_ids = get_objects_for_user(
            user, 'spaces.can_manage_bookable_amenity', klass=BookableAmenity
        ).values_list('id', flat=True)

        return qs.filter(
            Q(space__id__in=managed_spaces_ids) | Q(bookable_amenity__id__in=managed_amenities_ids)
        )

    # get_target_space_for_booking 和 create_booking 等通用方法可以保持不变或整合进service层
    # 如果 create 不需要特殊逻辑，可以直接调用 self.create(**kwargs)
    # create_booking 留在这里如果它提供了特定于Booking的便捷接口

    def get_target_space_for_booking(self, booking: Booking):
        # 这个方法不直接访问数据库，可以保持在DAO或移动到Service
        return booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)

    # 对于 create_booking，如果它只是简单地调用 model.objects.create，可以改为直接使用 self.create
    # 如果有额外的逻辑，例如设置默认值，则保留它
    def create_booking(self, user: CustomUser, space=None, amenity=None, quantity=1,
                       purpose="", start_time=None, end_time=None):
        return self.create( # 调用BaseDAO的create方法
            user=user,
            space=space,
            bookable_amenity=amenity,
            booked_quantity=quantity,
            purpose=purpose,
            start_time=start_time,
            end_time=end_time
        )