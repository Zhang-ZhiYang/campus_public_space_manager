# bookings/dao/booking_dao.py
from typing import Optional

from django.db.models import QuerySet
from core.dao import BaseDAO
from bookings.models import Booking
from spaces.models import Space, BookableAmenity, CustomUser  # 确保导入 Space 和 BookableAmenity
from django.utils import timezone # 导入 timezone

class BookingDAO(BaseDAO):
    model = Booking

    def get_queryset(self) -> QuerySet[Booking]:
        """
        获取基础 Booking QuerySet，并预加载常用关联对象以优化查询。
        """
        return super().get_queryset().select_related(
            'user',
            'space__space_type',
            'bookable_amenity__amenity',
            'bookable_amenity__space__space_type',
            'reviewed_by'
        )

    def get_booking_by_id(self, booking_id: int) -> Optional[Booking]:
        """根据ID获取单个预订记录。"""
        try:
            return self.get_queryset().get(pk=booking_id)
        except Booking.DoesNotExist:
            return None

    def create_booking(self, **kwargs) -> Booking:
        """
        创建新的 Booking 实例。
        确保调用实例的 full_clean() 和 save()，以便触发表单校验和模型信号。
        """
        instance = self.model(**kwargs)
        instance.full_clean()  # 触发 Booking 模型的 clean 方法，包括预订冲突和禁用检查
        instance.save()  # 触发 post_save 信号（如果 Booking 有的话）
        return instance

    def update_booking(self, booking_instance: Booking, **kwargs) -> Booking:
        """
        更新现有的 Booking 实例。
        确保调用实例的 full_clean() 和 save()，以便触发表单校验和模型信号。
        """
        for attr, value in kwargs.items():
            setattr(booking_instance, attr, value)
        booking_instance.full_clean()
        booking_instance.save()
        return booking_instance

    def update_booking_status(self, booking_instance: Booking, new_status: str,
                             admin_user: Optional[CustomUser] = None, admin_notes: Optional[str] = None) -> Booking:
        """
        专门用于更新预订状态的方法，会自动处理 reviewed_by 和 reviewed_at 字段。
        """
        booking_instance.status = new_status
        if admin_user:
            booking_instance.reviewed_by = admin_user
            booking_instance.reviewed_at = timezone.now()
        if admin_notes is not None:
            booking_instance.admin_notes = admin_notes
        booking_instance.full_clean()
        booking_instance.save()
        return booking_instance

    def delete_booking(self, booking_instance: Booking) -> None:
        """
        删除指定的 Booking 实例。
        """
        booking_instance.delete()

    def get_target_space_for_booking(self, booking: Booking) -> Optional[Space]:
        """
        根据 Booking 实例，返回它所针对的 Space 对象。
        无论是直接预订空间还是预订空间内的设施，都返回其父空间。
        """
        if booking.space:
            return booking.space
        if booking.bookable_amenity and booking.bookable_amenity.space:
            return booking.bookable_amenity.space
        return None