# bookings/dao/booking_dao.py
import logging
from datetime import datetime, date
from typing import Optional, List, Union, Any, Dict
import uuid

from django.db.models import QuerySet, Q
from django.utils import timezone

from core.dao import BaseDAO
from bookings.models import Booking
from spaces.models import Space, BookableAmenity, SpaceType
from users.models import CustomUser

logger = logging.getLogger(__name__)


class BookingDAO(BaseDAO):
    """
    数据访问对象，用于对 Booking 模型进行CRUD操作。
    此版本中的状态更新方法（update_booking_status, update_booking_processing_status）
    已优化为使用 QuerySet.update() 以提高性能和避免模型 save() 方法中的自定义逻辑副作用。
    DAO 层不处理业务异常。
    """
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
            'related_space__space_type',
            'reviewed_by'
        ).prefetch_related(
            'space__permitted_groups',
            'bookable_amenity__space__permitted_groups'
        )

    def get_booking_by_id(self, booking_id: int) -> Optional[Booking]:
        """根据ID获取单个预订记录。如果不存在则返回 None。"""
        try:
            return self.get_queryset().get(pk=booking_id)
        except Booking.DoesNotExist:
            logger.debug(f"Booking with ID {booking_id} not found.")
            return None

    def get_booking_by_request_uuid(self, request_uuid: Union[str, uuid.UUID]) -> Optional[Booking]:
        """根据请求唯一标识 (request_uuid) 获取单个预订记录。如果不存在则返回 None。"""
        try:
            return self.get_queryset().get(request_uuid=request_uuid)
        except Booking.DoesNotExist:
            logger.debug(f"Booking with request_uuid {request_uuid} not found.")
            return None

    def create_booking(self, **kwargs) -> Booking:
        """
        创建新的 Booking 实例。
        确保调用实例的 save()，它会触发模型中自定义的 full_clean()。
        """
        instance = self.model(**kwargs)
        instance.save()  # 模型中的save方法会调用full_clean()
        logger.info(
            f"Booking {instance.pk} created for user {instance.user.pk} with processing_status {instance.processing_status}.")
        return instance

    def update_booking(self, booking_instance: Booking, **kwargs) -> Booking:
        """
        更新现有的 Booking 实例的非状态类字段。
        此方法通常由 Service 层调用，用于更新除 processing_status/status 以外的字段。
        确保调用实例的 save()，它会触发模型中自定义的 full_clean()。
        """
        for attr, value in kwargs.items():
            setattr(booking_instance, attr, value)
        booking_instance.save()  # 模型中的save方法会调用full_clean()
        logger.info(f"Booking {booking_instance.pk} updated.")
        return booking_instance

    def update_booking_status(self, booking_id: int, new_status: str,
                              admin_user: Optional[CustomUser] = None, admin_notes: Optional[str] = None) -> Optional[
        Booking]:
        """
        专门用于更新预订业务状态的方法，会自动设置 reviewed_by 和 reviewed_at 字段。
        此方法直接使用 QuerySet.update()，避免了模型实例 save() 方法的副作用和开销。

        :param booking_id: 要更新的预订的ID。
        :param new_status: 新的预订状态。
        :param admin_user: 可选，执行审核的管理员用户实例。
        :param admin_notes: 可选，管理员对本次预订的备注。
        :return: 更新后的 Booking 实例，如果预订不存在则返回 None。
        """
        update_fields = {
            'status': new_status,
            'updated_at': timezone.now()
        }
        if admin_user:
            update_fields['reviewed_by'] = admin_user
            update_fields['reviewed_at'] = timezone.now()
        if admin_notes is not None:
            update_fields['admin_notes'] = admin_notes

        # 执行数据库更新
        rows_affected = self.model.objects.filter(pk=booking_id).update(**update_fields)

        # 无论 rows_affected 是否为0，都尝试重新获取更新后的实例。
        # 如果 booking_id 不存在，get_booking_by_id() 将返回 None。
        updated_booking = self.get_booking_by_id(booking_id)

        # 记录日志，让 Service 层判断 rows_affected 的业务含义
        if rows_affected > 0:
            logger.info(
                f"Booking {booking_id} status updated to {new_status} by {admin_user.username if admin_user else 'System'} using QuerySet.update().")
        else:
            logger.warning(
                f"Booking ID {booking_id} update via QuerySet.update() affected 0 rows. (Maybe ID not found or no change applied.)")

        return updated_booking

    def update_booking_processing_status(self, booking_id: int, new_processing_status: str,
                                         admin_notes: Optional[str] = None,
                                         new_booking_status: Optional[str] = None) -> Optional[Booking]:
        """
        专门用于更新预订的处理状态和可选的业务状态。
        此方法直接使用 QuerySet.update()，避免了模型实例 save() 方法的副作用和开销。

        :param booking_id: 要更新的预订的ID。
        :param new_processing_status: 新的处理状态。
        :param admin_notes: 可选，管理员对本次预订的备注。
        :param new_booking_status: 可选，如果同时需要更新业务状态，则传入新的业务状态。
        :return: 更新后的 Booking 实例，如果预订不存在则返回 None。
        """
        update_fields = {
            'processing_status': new_processing_status,
            'updated_at': timezone.now()
        }
        if new_booking_status:
            update_fields['status'] = new_booking_status
        if admin_notes is not None:
            update_fields['admin_notes'] = admin_notes

        # 执行数据库更新
        rows_affected = self.model.objects.filter(pk=booking_id).update(**update_fields)

        # 无论 rows_affected 是否为0，都尝试重新获取更新后的实例。
        # 如果 booking_id 不存在，get_booking_by_id() 将返回 None。
        updated_booking = self.get_booking_by_id(booking_id)

        # 记录日志，让 Service 层判断 rows_affected 的业务含义
        if rows_affected > 0:
            logger.info(
                f"Booking {booking_id} processing status updated to {new_processing_status} using QuerySet.update().")
        else:
            logger.warning(
                f"Booking ID {booking_id} processing status update via QuerySet.update() affected 0 rows. (Maybe ID not found or no change applied.)")

        return updated_booking

    def delete_booking(self, booking_instance: Booking) -> None:
        """
        删除指定的 Booking 实例。
        """
        pk = booking_instance.pk
        booking_instance.delete()
        logger.info(f"Booking {pk} deleted.")

    def get_target_space_for_booking(self, booking: Booking) -> Optional[Space]:
        """
        根据 Booking 实例，返回它所针对的 Space 对象。
        无论是直接预订空间还是预订空间内的设施，都返回其父空间。
        """
        return booking.related_space

    def get_user_bookings_count_for_date(self, user: CustomUser, target_date: date, status_in: List[str],
                                         space_type: Optional[SpaceType] = None) -> int:
        """
        获取用户在指定日期内、特定空间类型下（或全局），处于指定状态的预订数量。
        """
        start_of_day = timezone.make_aware(datetime.combine(target_date, datetime.min.time()))
        end_of_day = timezone.make_aware(datetime.combine(target_date, datetime.max.time()))

        filters = Q(
            user=user,
            start_time__gte=start_of_day,
            start_time__lte=end_of_day,
            status__in=status_in
        )

        if space_type:
            filters &= Q(related_space__space_type=space_type)

        count = self.get_queryset().filter(filters).count()
        logger.debug(f"User {user.pk} has {count} bookings on {target_date.isoformat()} with status in {status_in} "
                     f"for space_type {space_type.pk if space_type else 'None'}.")
        return count

    def get_all_bookings(self, filter_conditions: Optional[Q] = None, filters: Optional[Dict[str, Any]] = None,
                         prefetch_related: Optional[List[str]] = None,
                         select_related: Optional[List[str]] = None) -> QuerySet[Booking]:
        """
        通用查询所有预订记录的方法，支持多种过滤、预加载和急加载。
        """
        qs = self.get_queryset()

        if prefetch_related:
            qs = qs.prefetch_related(*prefetch_related)
        if select_related:
            qs = qs.select_related(*select_related)

        if filter_conditions:
            qs = qs.filter(filter_conditions)

        if filters:
            qs = qs.filter(**filters)

        return qs

    def get_overlapping_bookings(self, target_entity: Union[Space, BookableAmenity],
                                 start_time: datetime, end_time: datetime,
                                 exclude_booking_id: Optional[int] = None) -> QuerySet[Booking]:
        """
        查找在指定时间段内与给定空间或可预订设施实例冲突的预订。
        冲突定义：预订时间段重叠，且状态为 'PENDING' 或 'APPROVED'。
        """

        q_time_overlap = Q(end_time__gt=start_time) & Q(start_time__lt=end_time)

        q_target = Q()
        if isinstance(target_entity, Space):
            q_target = Q(space=target_entity, bookable_amenity__isnull=True)
            logger.debug(f"Target entity is Space {target_entity.pk}. Querying for direct space bookings only.")
        elif isinstance(target_entity, BookableAmenity):
            q_target = Q(bookable_amenity=target_entity)
            logger.debug(
                f"Target entity is BookableAmenity {target_entity.pk}. Querying for direct amenity bookings only.")
        else:
            logger.error(f"Invalid target_entity type: {type(target_entity)}. Expected Space or BookableAmenity.")
            raise ValueError("Target entity must be a Space or BookableAmenity instance for conflict checks.")

        if not q_target:  # If target_entity is invalid and q_target is empty
            return self.get_queryset().none()

        active_booking_for_conflict_check_statuses = [
            Booking.BOOKING_STATUS_PENDING,
            Booking.BOOKING_STATUS_APPROVED,
        ]
        q_status = Q(status__in=active_booking_for_conflict_check_statuses)

        filter_conditions = q_time_overlap & q_target & q_status

        if exclude_booking_id:
            filter_conditions &= ~Q(pk=exclude_booking_id)

        queryset = self.get_queryset().filter(filter_conditions)

        logger.debug(
            f"get_overlapping_bookings for Target: {target_entity} (PK: {getattr(target_entity, 'pk', 'N/A')}), "
            f"Time: {start_time} - {end_time}, Exclude: {exclude_booking_id}. "
            f"Considering statuses: {active_booking_for_conflict_check_statuses}. "
            f"Found {queryset.count()} overlapping bookings.")

        for booking in queryset:
            logger.debug(
                f" - Overlap: Booking ID={booking.pk}, Status={booking.status}, Quantity={booking.booked_quantity}, Range={booking.start_time.isoformat()}-{booking.end_time.isoformat()}")

        return queryset

    def get_user_total_bookings_count_for_date(self, user: CustomUser, target_date: date, status_in: List[str]) -> int:
        """
        获取用户在指定日期内，处于指定状态的预订总数量（不分空间类型）。
        """
        start_of_day = timezone.make_aware(datetime.combine(target_date, datetime.min.time()))
        end_of_day = timezone.make_aware(datetime.combine(target_date, datetime.max.time()))

        count = self.get_queryset().filter(
            user=user,
            start_time__gte=start_of_day,
            start_time__lte=end_of_day,
            status__in=status_in
        ).count()
        logger.debug(
            f"User {user.pk} has {count} total bookings on {target_date.isoformat()} with status in {status_in} across all space types.")
        return count