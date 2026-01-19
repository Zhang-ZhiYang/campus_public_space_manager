# bookings/dao/booking_dao.py
from datetime import datetime
from typing import Optional, List, Union
import uuid

from django.db.models import QuerySet, Q
from core.dao import BaseDAO
from bookings.models import Booking
from spaces.models import Space, BookableAmenity # 需要 Space 和 BookableAmenity 用于类型提示和查询
from users.models import CustomUser
from django.utils import timezone

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
            'related_space__space_type', # NEW: 确保预加载 related_space 的 space_type
            'reviewed_by'
        ).prefetch_related(
            'space__permitted_groups',
            'bookable_amenity__space__permitted_groups'
        )

    def get_booking_by_id(self, booking_id: int) -> Optional[Booking]:
        """根据ID获取单个预订记录。"""
        try:
            return self.get_queryset().get(pk=booking_id)
        except Booking.DoesNotExist:
            return None

    # NEW: 根据 request_uuid 获取预订，用于幂等性检查
    def get_booking_by_request_uuid(self, request_uuid: Union[str, uuid.UUID]) -> Optional[Booking]:
        """根据请求唯一标识 (request_uuid) 获取单个预订记录。"""
        try:
            return self.get_queryset().get(request_uuid=request_uuid)
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
        # 由于 Booking 模型现在有 related_space 字段，可以直接返回
        return booking.related_space

    def get_user_bookings_count_for_date(self, user: CustomUser, date: timezone.localdate, status_in: List[str]) -> int:
        """
        获取用户在指定日期内，处于指定状态的预订数量。
        """
        return self.get_queryset().filter(
            user=user,
            start_time__date=date,
            status__in=status_in
            # TODO: 未来DailyBookingLimitService可能需要按space_type进行计数，此处可能需修改
        ).count()


    def get_overlapping_bookings(self, target_entity: Union[Space, BookableAmenity],
                                 start_time: datetime, end_time: datetime,
                                 exclude_booking_id: Optional[int] = None) -> QuerySet[Booking]:
        """
        查找在指定时间段内与给定空间或可预订设施实例冲突的活跃预订。
        冲突定义：预订时间段重叠，且不包括自身。
        """
        query = Q(end_time__gt=start_time, start_time__lt=end_time) # 时间段重叠条件

        if isinstance(target_entity, Space):
            query &= Q(space=target_entity, bookable_amenity__isnull=True) # 仅预订整个空间
            # Also need to check if there are any bookings of bookable amenities within this space that conflict
            # This makes the logic complex. For simplicity, assume booking space and booking amenity are mutually exclusive direct targets
            # A space booking for the whole space should prevent any amenity booking within it.
            # An amenity booking should not prevent a whole space booking unless explicitly modeled.
            # Based on the architecture plan: "预订只能指定一个目标：空间或设施实例。"
            # So, we only need to check what 'target_entity' refers to directly.
            # If a space is a container, it usually can't be booked directly, but its amenities can.
            # If it's a bookable space, then it conflicts with other bookings of itself.
        elif isinstance(target_entity, BookableAmenity):
            query &= Q(bookable_amenity=target_entity)
            # For bookable amenities, we need to check if the booked_quantity is exhausted
            # This is more complex than a simple overlap check.
            # A simple overlap check would return ALL overlapping bookings. The service layer
            # would then sum quantities and determine true conflicts.

            # For now, this DAO simply gets ALL overlapping bookings for THIS specific amenity instance.
            # The higher layer (BookingValidationCreationService) will handle quantity logic.
        else:
            raise ValueError("target_entity must be a Space or BookableAmenity instance.")

        # Exclude currently processed booking if it's an update scenario
        if exclude_booking_id:
            query &= ~Q(pk=exclude_booking_id)

        # Only consider active status bookings for conflict (PENDING, APPROVED, CHECKED_IN)
        query &= Q(status__in=['PENDING', 'APPROVED', 'CHECKED_IN'])

        return self.get_queryset().filter(query)
    def update_booking_processing_status(self, booking_instance: Booking, new_processing_status: str,
                                         admin_notes: Optional[str] = None,
                                         new_booking_status: Optional[str] = None) -> Booking:
        """
        专门用于更新预订的处理状态和可选的业务状态。
        :param booking_instance: 要更新的 Booking 实例。
        :param new_processing_status: 新的异步处理状态（例如 'IN_PROGRESS', 'FAILED_VALIDATION'）。
        :param admin_notes: 可选的管理员备注信息。
        :param new_booking_status: 可选的新的业务状态（例如 'PENDING', 'APPROVED', 'REJECTED'）。
        :return: 更新后的 Booking 实例。
        """
        booking_instance.processing_status = new_processing_status
        if new_booking_status:
            booking_instance.status = new_booking_status
        if admin_notes is not None:
            booking_instance.admin_notes = admin_notes
        booking_instance.full_clean() # 再次运行 clean 方法确保数据一致性
        booking_instance.save()
        return booking_instance