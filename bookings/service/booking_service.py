# bookings/service/booking_service.py
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from typing import Dict, Any, Optional, List
from django.contrib.auth import get_user_model

from bookings.models import Booking
from spaces.models import Space, BookableAmenity
from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, ServiceException
from core.service.factory import ServiceFactory  # 导入 ServiceFactory

# 获取 CustomUser 模型
CustomUser = get_user_model()


class BookingService(BaseService):
    _dao_map = {
        'booking_dao': 'booking',
    }

    def __init__(self):
        super().__init__()
        # 移除对 ViolationService 的直接引用，因为它现在与取消逻辑解耦了
        # self.violation_service = ServiceFactory.get_service('ViolationService')

    @transaction.atomic
    def create_booking(self, user: CustomUser, booking_data: Dict[str, Any]) -> ServiceResult[Booking]:
        """
        创建新的预订，包含权限检查和业务逻辑校验。
        booking_data 应包含 'space_id' 或 'bookable_amenity_id'。
        其他字段如 'start_time', 'end_time', 'purpose', 'booked_quantity' 等。
        """
        space_id = booking_data.pop('space_id', None)
        bookable_amenity_id = booking_data.pop('bookable_amenity_id', None)

        target_space: Optional[Space] = None
        target_amenity: Optional[BookableAmenity] = None

        if space_id:
            target_space = Space.objects.select_related('space_type').prefetch_related('restricted_groups').filter(
                pk=space_id).first()
            if not target_space:
                return ServiceResult.error_result(
                    message="空间不存在。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # ====== 权限和可用性检查：预订整个空间 ======
            if not user.has_perm('spaces.can_book_this_space'):
                return ServiceResult.error_result(
                    message=f"您没有权限预订任何空间。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )
            if not target_space.is_bookable or not target_space.is_active:
                return ServiceResult.error_result(
                    message=f"空间 '{target_space.name}' 不可预订或未启用。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
            if user.groups.filter(pk__in=target_space.restricted_groups.values_list('pk', flat=True)).exists():
                return ServiceResult.error_result(
                    message=f"抱歉，您所属的用户组被限制预订空间 '{target_space.name}'。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )
            booking_data['space'] = target_space

        elif bookable_amenity_id:
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
            if not user.has_perm('spaces.can_book_amenities_in_space'):
                return ServiceResult.error_result(
                    message=f"您没有权限预订任何设施。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )
            if not target_amenity.is_bookable or not target_amenity.is_active:
                return ServiceResult.error_result(
                    message=f"设施 '{target_amenity.amenity.name}' (在 '{parent_space.name}' 中) 不可预订或未启用。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
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

        booking_data['user'] = user

        try:
            new_booking = self.booking_dao.create_booking(**booking_data)
            return ServiceResult.success_result(
                data=new_booking,
                message="预订请求已提交。",
                status_code=201
            )
        except Exception as e:
            return self._handle_exception(e, default_message=f"创建预订失败: {e}",
                                          default_status_code=BadRequestException.status_code)

    def get_user_bookings(self, user: CustomUser) -> QuerySet[Booking]:
        """
        获取用户的所有预订记录。
        """
        return self.booking_dao.get_queryset().filter(user=user)

    def get_all_bookings(self, user: CustomUser) -> ServiceResult[QuerySet[Booking]]:
        """
        管理员/系统管理员获取所有预订记录。
        """
        if not (user.is_superuser or user.is_system_admin or user.has_perm('bookings.can_approve_booking')):
            return ServiceResult.error_result(
                message="您没有权限查看所有的预订记录。",
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )
        return ServiceResult.success_result(
            data=self.booking_dao.get_queryset(),
            message="成功获取所有预订记录。"
        )

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
            target_space = self.booking_dao.get_target_space_for_booking(booking)
            if not (target_space and user.has_perm('spaces.can_manage_space_bookings', target_space)):
                return ServiceResult.error_result(
                    message="您没有权限取消此预订。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

        if booking.status in ['CANCELLED', 'COMPLETED', 'REJECTED', 'NO_SHOW', 'CHECKED_IN', 'CHECKED_OUT']:
            return ServiceResult.error_result(
                message=f"预订 '{booking_id}' 状态为 '{booking.get_status_display()}'，无法取消。",
                error_code=BadRequestException.default_code,
                status_code=BadRequestException.status_code
            )

        # 移除取消惩罚逻辑

        booking.status = 'CANCELLED'
        try:
            updated_booking = self.booking_dao.update_booking_status(booking, 'CANCELLED', admin_user=user,
                                                                     admin_notes="用户或管理员取消预订")
            return ServiceResult.success_result(data=updated_booking, message="预订已取消。", status_code=200)
        except Exception as e:
            return self._handle_exception(e, default_message=f"取消预订失败: {e}",
                                          default_status_code=BadRequestException.status_code)

    @transaction.atomic
    def update_booking_status(self, user: CustomUser, booking_id: int, new_status: str,
                              admin_notes: Optional[str] = None) -> ServiceResult[Booking]:
        """
        通用更新预订状态的方法（批准、拒绝、签到、签出）。
        进行权限检查。
        """
        booking = self.booking_dao.get_booking_by_id(booking_id)
        if not booking:
            return ServiceResult.error_result(
                message="预订不存在。",
                error_code=NotFoundException.default_code,
                status_code=NotFoundException.status_code
            )

        target_space = self.booking_dao.get_target_space_for_booking(booking)

        # 权限检查：只有超级管理员、系统管理员或拥有特定权限的用户才能执行这些操作
        has_permission = False
        if user.is_superuser or user.is_system_admin:
            has_permission = True
        elif target_space and user.has_perm('spaces.can_manage_space_bookings', target_space):
            has_permission = True

        # 批准/拒绝权限
        if new_status in ['APPROVED', 'REJECTED'] and not user.has_perm('bookings.can_approve_booking'):
            has_permission = False

        # 签到/签出权限
        if new_status in ['CHECKED_IN', 'CHECKED_OUT', 'NO_SHOW'] and not user.has_perm(
                'bookings.can_check_in_booking'):
            has_permission = False

        if not has_permission:
            return ServiceResult.error_result(
                message="您没有权限执行此预订状态操作。",
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )

        # 业务逻辑：根据状态转换规则进行校验
        if new_status == 'APPROVED':
            if booking.status not in ['PENDING']:
                return ServiceResult.error_result(
                    message=f"预订当前状态为 '{booking.get_status_display()}'，无法批准。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
        elif new_status == 'REJECTED':
            if booking.status not in ['PENDING']:
                return ServiceResult.error_result(
                    message=f"预订当前状态为 '{booking.get_status_display()}'，无法拒绝。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
        elif new_status == 'CHECKED_IN':
            if booking.status not in ['APPROVED']:
                return ServiceResult.error_result(
                    message=f"预订当前状态为 '{booking.get_status_display()}'，无法签到。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
            if timezone.now() > booking.end_time:
                return ServiceResult.error_result(
                    message="预订已结束，无法签到。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
            # 可以添加签到时间窗检查
        elif new_status == 'CHECKED_OUT':
            if booking.status not in ['CHECKED_IN']:
                return ServiceResult.error_result(
                    message=f"预订当前状态为 '{booking.get_status_display()}'，无法签出。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
        elif new_status == 'NO_SHOW':
            # 未到场一般是系统或管理员在预订结束后标记
            if booking.status not in ['PENDING', 'APPROVED', 'CHECKED_IN']:  # 可以在签到前，也可以在批准后
                return ServiceResult.error_result(
                    message=f"预订当前状态为 '{booking.get_status_display()}'，无法标记为未到场。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
            if timezone.now() < booking.end_time:
                return ServiceResult.error_result(
                    message="预订尚未结束，无法标记为未到场。",  # 通常未到场是在预订结束或开始后一段时间内才标记的
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
        elif new_status == 'COMPLETED':
            if booking.status not in ['CHECKED_OUT']:  # 只有签出后才能完成
                return ServiceResult.error_result(
                    message=f"预订当前状态为 '{booking.get_status_display()}'，无法标记为已完成。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )

        else:  # 不支持的其他状态
            return ServiceResult.error_result(
                message=f"不支持更新为状态 '{new_status}'。",
                error_code=BadRequestException.default_code,
                status_code=BadRequestException.status_code
            )

        try:
            updated_booking = self.booking_dao.update_booking_status(booking, new_status, admin_user=user,
                                                                     admin_notes=admin_notes)
            return ServiceResult.success_result(
                data=updated_booking,
                message=f"预订状态已更新为 '{updated_booking.get_status_display()}'。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message=f"更新预订状态失败: {e}",
                                          default_status_code=BadRequestException.status_code)