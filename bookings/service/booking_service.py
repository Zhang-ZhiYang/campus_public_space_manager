# bookings/service/booking_service.py
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from datetime import datetime, time  # 导入 datetime 和 time
from typing import Dict, Any, Optional, List
from django.contrib.auth import get_user_model
import logging  # NEW: 导入 logging

from bookings.models import Booking
from spaces.models import Space, BookableAmenity
from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, ServiceException
from core.service.factory import ServiceFactory  # 导入 ServiceFactory
from spaces.service.space_service import SpaceService  # NEW: 导入 SpaceService

logger = logging.getLogger(__name__)  # 获取 logger 实例

# 获取 CustomUser 模型
CustomUser = get_user_model()


class BookingService(BaseService):
    _dao_map = {
        'booking_dao': 'booking',
        'daily_booking_limit_dao': 'daily_booking_limit',
    }

    def __init__(self):
        super().__init__()
        # NEW: 获取 SpaceService 实例
        self.space_service: SpaceService = ServiceFactory.get_service('SpaceService')
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
        requires_approval = True  # 默认需要审批

        if space_id:
            # === 关键点: 使用 SpaceService 来获取空间并执行所有必要的权限检查 ===
            logger.debug(
                f"BookingService: User {user.username} (ID:{user.id}) attempting to book space_id={space_id}. Calling SpaceService.get_space_by_id...")
            space_access_result = self.space_service.get_space_by_id(user, space_id)

            if not space_access_result.success:
                # 如果 SpaceService 判定用户无权访问此空间，则直接返回其错误
                logger.warning(
                    f"BookingService: User {user.username} was denied booking for space {space_id} by SpaceService. Message: {space_access_result.message}")
                return ServiceResult.error_result(
                    message=space_access_result.message,
                    error_code=space_access_result.error_code,
                    status_code=space_access_result.status_code
                )
            target_space = space_access_result.data  # 确保获取到通过权限检查的空间对象

            # 额外的检查：确认空间本身是否被标记为可预订且活跃 (SpaceService.get_space_by_id 也做了类似检查)
            if not target_space.is_bookable or not target_space.is_active:
                logger.warning(
                    f"BookingService: Space {target_space.name} (ID:{target_space.id}) is not bookable or not active, despite being accessible.")
                return ServiceResult.error_result(
                    message=f"空间 '{target_space.name}' 不可预订或未启用。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )

            # 旧的 `restricted_groups` 检查已被 SpaceService.get_space_by_id 中的全面权限逻辑取代
            # if user.groups.filter(pk__in=target_space.restricted_groups.values_list('pk', flat=True)).exists():
            #    return ServiceResult.error_result(...)

            booking_data['space'] = target_space
            requires_approval = target_space.requires_approval  # 获取空间的审批需求

        elif bookable_amenity_id:
            # 对于设施预订，首先定位可预订设施及其所属空间
            # 直接从 DAO 获取，但之后仍需使用 SpaceService 检查父空间的权限
            target_amenity = BookableAmenity.objects.select_related(
                'amenity', 'space__space_type'
            ).prefetch_related('space__permitted_groups').filter(pk=bookable_amenity_id).first()

            if not target_amenity:
                logger.debug(f"BookingService: BookableAmenity with ID {bookable_amenity_id} not found.")
                return ServiceResult.error_result(
                    message="可预订设施实例不存在。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            parent_space = target_amenity.space
            if not parent_space:
                logger.critical(
                    f"BookingService: BookableAmenity {bookable_amenity_id} found but its parent space is None. This indicates data inconsistency.")
                return ServiceResult.error_result(
                    message="可预订设施所属空间不存在。",
                    error_code=NotFoundException.default_code,  # 如果此状态不应发生，则可能是 Internal Server Error
                    status_code=NotFoundException.status_code
                )

            # === 关键点: 对于设施预订，也需要检查用户对其所属父空间的访问权限 ===
            logger.debug(
                f"BookingService: User {user.username} (ID:{user.id}) attempting to book amenity {bookable_amenity_id} within parent space_id={parent_space.id}. Calling SpaceService.get_space_by_id for parent space...")
            space_access_result = self.space_service.get_space_by_id(user, parent_space.id)
            if not space_access_result.success:
                logger.warning(
                    f"BookingService: User {user.username} was denied booking for amenity {bookable_amenity_id} due to parent space {parent_space.id} access denial by SpaceService. Message: {space_access_result.message}")
                return ServiceResult.error_result(
                    message=space_access_result.message,
                    error_code=space_access_result.error_code,
                    status_code=space_access_result.status_code
                )
            # parent_space 对象无需从 space_access_result.data 重新赋值，因为我们已通过它获取了权限，且已经有完整的 parent_space 对象。

            # 额外的检查：确认设施本身是否被标记为可预订且活跃
            if not target_amenity.is_bookable or not target_amenity.is_active:
                logger.warning(
                    f"BookingService: Amenity '{target_amenity.amenity.name}' (ID:{target_amenity.id}) in space {parent_space.name} is not bookable or not active.")
                return ServiceResult.error_result(
                    message=f"设施 '{target_amenity.amenity.name}' (在 '{parent_space.name}' 中) 不可预订或未启用。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )

            # 旧的 `restricted_groups` 检查已被 SpaceService.get_space_by_id 中的全面权限逻辑取代

            booking_data['bookable_amenity'] = target_amenity
            booking_data['space'] = parent_space  # 确保预订实例关联到父空间
            requires_approval = parent_space.requires_approval  # 获取设施所在空间的审批需求
        else:
            logger.warning("BookingService: Create booking request received without space_id or bookable_amenity_id.")
            return ServiceResult.error_result(
                message="预订必须指定一个空间或可预订设施。",
                error_code=BadRequestException.default_code,
                status_code=BadRequestException.status_code
            )

        booking_data['user'] = user

        # 后续逻辑（每日预订限制、自动化审批）保持不变
        # ====== 每日预订次数限制检查 - START ======
        effective_max_daily_bookings = float('inf')  # 初始化为无限，表示无限制

        user_group_ids = list(user.groups.values_list('id', flat=True))

        if user_group_ids:  # 只有当用户属于某个组时才检查限制
            active_limits = self.daily_booking_limit_dao.get_active_limits_for_group_ids(user_group_ids)

            # 遍历所有适用的限制，取最严格的（最小值）
            for limit_obj in active_limits:
                effective_max_daily_bookings = min(effective_max_daily_bookings, limit_obj.max_bookings)

        # 如果 effective_max_daily_bookings 仍然是 float('inf')，表示用户所属的组都没有设置限制，或者所有限制都是0（不限制）
        if effective_max_daily_bookings != float('inf'):
            today = timezone.localdate()
            bookings_today_count = self.booking_dao.get_user_bookings_count_for_date(
                user, today, status_in=['PENDING', 'APPROVED', 'CHECKED_IN']
            )

            if bookings_today_count >= effective_max_daily_bookings:
                logger.warning(
                    f"BookingService: User {user.username} exceeded daily booking limit ({int(effective_max_daily_bookings)}). Current bookings: {bookings_today_count}")
                return ServiceResult.error_result(
                    message=f"您今天已达到最大预订次数限制 ({int(effective_max_daily_bookings)} 次)。请明天再尝试。",
                    error_code="DAILY_LIMIT_EXCEEDED",
                    status_code=BadRequestException.default_code
                )
        # ====== 每日预订次数限制检查 - END ======

        # ====== 自动化审批逻辑 - START ======
        if not requires_approval:
            booking_data['status'] = 'APPROVED'
            # 自动批准的预订，需要明确设置 reviewed_by 和 reviewed_at
            booking_data['reviewed_by'] = None  # 表示系统自动审批，而非特定用户
            booking_data['reviewed_at'] = timezone.now()
            message_on_success = "预订已成功批准。"
            logger.info(
                f"BookingService: Booking for user {user.username} of space/amenity {target_space.name if target_space else (target_amenity.amenity.name if target_amenity else 'N/A')} auto-approved.")
        else:
            booking_data['status'] = 'PENDING'  # 默认就是 PENDING，这里明确写出
            message_on_success = "预订请求已提交，等待审核。"
            logger.info(
                f"BookingService: Booking for user {user.username} of space/amenity {target_space.name if target_space else (target_amenity.amenity.name if target_amenity else 'N/A')} submitted for approval.")
        # ====== 自动化审批逻辑 - END ======

        try:
            # create_booking 方法在 DAO 层调用了 full_clean() 和 save()
            new_booking = self.booking_dao.create_booking(**booking_data)

            return ServiceResult.success_result(
                data=new_booking,
                message=message_on_success,
                status_code=201
            )
        except Exception as e:
            logger.exception(f"BookingService: Failed to create booking for user {user.username}.")
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
            logger.debug(f"BookingService: Attempted to cancel non-existent booking with ID {booking_id}.")
            return ServiceResult.error_result(
                message="预订不存在。",
                error_code=NotFoundException.default_code,
                status_code=NotFoundException.status_code
            )

        # 权限检查：用户只能取消自己的预订，或有管理权限的管理员可以取消
        if booking.user != user and not (user.is_superuser or user.is_system_admin):
            target_space = self.booking_dao.get_target_space_for_booking(booking)
            if not (target_space and user.has_perm('spaces.can_manage_space_bookings', target_space)):
                logger.warning(
                    f"BookingService: User {user.username} tried to cancel booking {booking_id} (owner: {booking.user.username}) without sufficient permission.")
                return ServiceResult.error_result(
                    message="您没有权限取消此预订。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

        if booking.status in ['CANCELLED', 'COMPLETED', 'REJECTED', 'NO_SHOW', 'CHECKED_IN', 'CHECKED_OUT']:
            logger.warning(
                f"BookingService: Booking {booking_id} is in status '{booking.get_status_display()}', cannot be cancelled.")
            return ServiceResult.error_result(
                message=f"预订 '{booking_id}' 状态为 '{booking.get_status_display()}'，无法取消。",
                error_code=BadRequestException.default_code,
                status_code=BadRequestException.status_code
            )

        try:
            updated_booking = self.booking_dao.update_booking_status(booking, 'CANCELLED', admin_user=user,
                                                                     admin_notes="用户或管理员取消预订")
            logger.info(f"BookingService: Booking {booking_id} cancelled by user {user.username}.")
            return ServiceResult.success_result(data=updated_booking, message="预订已取消。", status_code=200)
        except Exception as e:
            logger.exception(f"BookingService: Failed to cancel booking {booking_id} by user {user.username}.")
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
            logger.debug(f"BookingService: Attempted to update status for non-existent booking with ID {booking_id}.")
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
            logger.warning(
                f"BookingService: User {user.username} (ID:{user.id}) attempted to change status of booking {booking_id} to {new_status} without sufficient permission.")
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
            if timezone.now() > booking.end_time:  # 考虑预订时间窗
                return ServiceResult.error_result(
                    message="预订已结束，无法签到。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
        elif new_status == 'CHECKED_OUT':
            if booking.status not in ['CHECKED_IN']:
                return ServiceResult.error_result(
                    message=f"预订当前状态为 '{booking.get_status_display()}'，无法签出。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
        elif new_status == 'NO_SHOW':
            if booking.status not in ['PENDING', 'APPROVED', 'CHECKED_IN']:
                return ServiceResult.error_result(
                    message=f"预订当前状态为 '{booking.get_status_display()}'，无法标记为未到场。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
            if timezone.now() < booking.end_time:
                return ServiceResult.error_result(
                    message="预订尚未结束，无法标记为未到场。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )
        elif new_status == 'COMPLETED':
            if booking.status not in ['CHECKED_OUT', 'NO_SHOW']:  # 允许 NO_SHOW 后也标记为 COMPLETED
                return ServiceResult.error_result(
                    message=f"预订当前状态为 '{booking.get_status_display()}'，无法标记为已完成。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )

        else:
            return ServiceResult.error_result(
                message=f"不支持更新为状态 '{new_status}'。",
                error_code=BadRequestException.default_code,
                status_code=BadRequestException.status_code
            )

        try:
            updated_booking = self.booking_dao.update_booking_status(booking, new_status, admin_user=user,
                                                                     admin_notes=admin_notes)
            logger.info(f"BookingService: Booking {booking_id} status updated to {new_status} by user {user.username}.")
            return ServiceResult.success_result(
                data=updated_booking,
                message=f"预订状态已更新为 '{updated_booking.get_status_display()}'。",
                status_code=200
            )
        except Exception as e:
            logger.exception(
                f"BookingService: Failed to update status for booking {booking_id} to {new_status} by user {user.username}.")
            return self._handle_exception(e, default_message=f"更新预订状态失败: {e}",
                                          default_status_code=BadRequestException.status_code)