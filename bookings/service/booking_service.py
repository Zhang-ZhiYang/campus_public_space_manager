# bookings/service/booking_service.py
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from typing import Dict, Any, Optional
from django.contrib.auth import get_user_model

from bookings.models import Booking
from spaces.models import Space, BookableAmenity
from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, ServiceException

# 获取 CustomUser 模型
CustomUser = get_user_model()

class BookingService(BaseService):
    _dao_map = {
        'booking_dao': 'booking',  # 映射到 bookings.dao.booking_dao.BookingDAO
    }

    @transaction.atomic
    def create_booking(self, user: CustomUser, booking_data: Dict[str, Any]) -> ServiceResult[Booking]:
        """
        创建新的预订，包含权限检查和业务逻辑校验。
        booking_data 应包含 'space_id' 或 'bookable_amenity_id'。
        其他字段如 'start_time', 'end_time', 'purpose', 'booked_quantity' 等。
        """
        space_id = booking_data.pop('space_id', None)
        bookable_amenity_id = booking_data.pop('bookable_amenity_id', None)

        # 预加载相关对象以减少查询
        target_space: Optional[Space] = None
        target_amenity: Optional[BookableAmenity] = None

        if space_id:
            target_space = Space.objects.select_related('space_type').prefetch_related('restricted_groups').filter(pk=space_id).first()
            if not target_space:
                return ServiceResult.error_result(
                    message="空间不存在。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # ====== 权限和可用性检查：预订整个空间 ======
            # FIX: 删除 target_space 参数，将其变为全局权限检查
            if not user.has_perm('spaces.can_book_this_space'):
                return ServiceResult.error_result(
                    message=f"您没有权限预订任何空间。", # 修改错误消息，使其更通用
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )
            if not target_space.is_bookable or not target_space.is_active:
                return ServiceResult.error_result(
                    message=f"空间 '{target_space.name}' 不可预订或未启用。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
            # 检查用户所属的组是否在 Restricted Groups 中
            if user.groups.filter(pk__in=target_space.restricted_groups.values_list('pk', flat=True)).exists():
                return ServiceResult.error_result(
                    message=f"抱歉，您所属的用户组被限制预订空间 '{target_space.name}'。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )
            booking_data['space'] = target_space

        elif bookable_amenity_id:
            # 预加载 BookableAmenity 及其关联的 Space 和 Amenity
            target_amenity = BookableAmenity.objects.select_related(
                'amenity', 'space__space_type'
            ).prefetch_related('space__restricted_groups').filter(pk=bookable_amenity_id).first()

            if not target_amenity:
                return ServiceResult.error_result(
                    message="可预订设施实例不存在。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            parent_space = target_amenity.space
            if not parent_space:
                return ServiceResult.error_result(
                    message="可预订设施所属空间不存在。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # ====== 权限和可用性检查：预订空间下的设施 ======
            # FIX: 删除 parent_space 参数，使其成为全局权限检查。
            # 这样，只要用户拥有 'spaces.can_book_amenities_in_space' 这个全局权限，
            # 这一步就会通过。后续的 restricted_groups 自然会提供对象级限制。
            if not user.has_perm('spaces.can_book_amenities_in_space'):
                # 调整错误消息，使其更符合全局权限缺失的语境
                return ServiceResult.error_result(
                    message=f"您没有权限预订任何设施。", # 修改错误消息
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )
            if not target_amenity.is_bookable or not target_amenity.is_active:
                return ServiceResult.error_result(
                    message=f"设施 '{target_amenity.amenity.name}' (在 '{parent_space.name}' 中) 不可预订或未启用。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
            # 设施预订是否继承父空间的受限组？通常是的。
            if user.groups.filter(pk__in=parent_space.restricted_groups.values_list('pk', flat=True)).exists():
                return ServiceResult.error_result(
                    message=f"抱歉，您所属的用户组被限制预订空间 '{parent_space.name}' 下的设施。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )
            booking_data['bookable_amenity'] = target_amenity
        else:
            return ServiceResult.error_result(
                message="预订必须指定一个空间或可预订设施。",
                error_code=BadRequestException.default_code,
                status_code=BadRequestException.status_code
            )

        # 填充其他必要的 Booking 字段
        booking_data['user'] = user

        try:
            new_booking = self.booking_dao.create_booking(**booking_data)
            return ServiceResult.success_result(
                data=new_booking,
                message="预订请求已提交。",
                status_code=201 # 现在 ServiceResult.success_result 支持 status_code
            )
        except Exception as e:
            # FIX: 将 status_code 改为 default_status_code
            return self._handle_exception(e, default_message=f"创建预订失败: {e}",
                                          default_status_code=BadRequestException.status_code)

    # TODO: 添加其他预订管理方法，如 get_user_bookings, cancel_booking, update_booking_status 等
    def get_user_bookings(self, user: CustomUser) -> QuerySet[Booking]:
        """
        获取用户的所有预订记录。
        """
        return self.booking_dao.get_queryset().filter(user=user)

    @transaction.atomic
    def cancel_booking(self, user: CustomUser, booking_id: int) -> ServiceResult[Booking]:
        """
        取消一个预订。需要权限检查。
        """
        booking = self.booking_dao.get_booking_by_id(booking_id)
        if not booking:
            return ServiceResult.error_result(
                message="预订不存在。",
                error_code=NotFoundException.default_code,
                status_code=NotFoundException.status_code
            )

        # 权限检查：用户只能取消自己的预订，或有管理权限的管理员可以取消
        if booking.user != user and not (user.is_superuser or user.is_system_admin):
            # 也可以进一步细化，检查空间管理员是否有权限取消其管理空间下的预订
            target_space = self.booking_dao.get_target_space_for_booking(booking)
            if not (target_space and user.has_perm('spaces.can_manage_space_bookings', target_space)):
                return ServiceResult.error_result(
                    message="您没有权限取消此预订。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

        if booking.status in ['CANCELLED', 'COMPLETED', 'REJECTED']:
            return ServiceResult.error_result(
                message=f"预订 '{booking_id}' 状态为 '{booking.get_status_display()}'，无法取消。",
                error_code=BadRequestException.default_code,
                status_code=BadRequestException.status_code
            )

        # TODO: 可以在这里添加取消惩罚逻辑

        booking.status = 'CANCELLED'
        try:
            updated_booking = self.booking_dao.update_booking(booking, status='CANCELLED')
            return ServiceResult.success_result(data=updated_booking, message="预订已取消。", status_code=200) # status_code 可以在取消时传入
        except Exception as e:
            # FIX: 将 status_code 改为 default_status_code
            return self._handle_exception(e, default_message=f"取消预订失败: {e}", default_status_code=BadRequestException.status_code)