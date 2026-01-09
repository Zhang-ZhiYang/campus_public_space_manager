# bookings/service/booking_service.py (修订版)
import logging
from typing import Dict, Any, Optional, List, Tuple
from django.db import transaction
from django.db.models import QuerySet, Q
from django.utils import timezone
from django.contrib.auth import get_user_model
from guardian.shortcuts import get_objects_for_user

from bookings.models import Booking
from spaces.models import Space, BookableAmenity
from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, ServiceException
from core.service.factory import ServiceFactory
from spaces.service.space_service import SpaceService

logger = logging.getLogger(__name__)

CustomUser = get_user_model()


class BookingService(BaseService):
    _dao_map = {
        'booking_dao': 'booking',
        'daily_booking_limit_dao': 'daily_booking_limit',
    }

    def __init__(self):
        super().__init__()
        self.space_service: SpaceService = ServiceFactory.get_service('SpaceService')

    # create_booking: 对象级预订权限不变，对 SpaceService 的依赖不变
    @transaction.atomic
    def create_booking(self, user: CustomUser, booking_data: Dict[str, Any]) -> ServiceResult[Booking]:
        """
        创建新的预订，包含对目标空间/设施的业务逻辑校验和访问权限检查。
        视图层确保用户已认证。
        """
        space_id = booking_data.pop('space_id', None)
        bookable_amenity_id = booking_data.pop('bookable_amenity_id', None)

        target_space: Optional[Space] = None
        target_amenity: Optional[BookableAmenity] = None
        requires_approval = True

        if space_id:
            # 使用 SpaceService 来获取空间并执行其查看权限检查
            # 注意：SpaceService.get_space_by_id 内部会检查 user 对 Space 的 can_view_space 权限
            space_access_result = self.space_service.get_space_by_id(user, space_id)

            if not space_access_result.success:
                return ServiceResult.error_result(
                    message=space_access_result.message,
                    error_code=space_access_result.error_code,
                    status_code=space_access_result.status_code
                )
            target_space = space_access_result.data

            # Additional business rules (bookable status)
            if not target_space.is_bookable or not target_space.is_active:
                logger.warning(
                    f"BookingService: Space {target_space.name} (ID:{target_space.id}) is not bookable or not active, despite being accessible.")
                return ServiceResult.error_result(
                    message=f"空间 '{target_space.name}' 不可预订或未启用。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )

            # 追加对象级预订权限检查
            if not (user.is_system_admin or user.has_perm('spaces.can_book_this_space', target_space)):
                return ServiceResult.error_result(
                    message="您没有权限预订此空间。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

            booking_data['space'] = target_space
            requires_approval = target_space.requires_approval

        elif bookable_amenity_id:
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
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # 对于设施预订，也需要检查用户对其所属父空间的查看权限
            space_access_result = self.space_service.get_space_by_id(user, parent_space.id)
            if not space_access_result.success:
                return ServiceResult.error_result(
                    message=space_access_result.message,
                    error_code=space_access_result.error_code,
                    status_code=space_access_result.status_code
                )

            # Additional business rules (bookable status)
            if not target_amenity.is_bookable or not target_amenity.is_active:
                logger.warning(
                    f"BookingService: Amenity '{target_amenity.amenity.name}' (ID:{target_amenity.id}) in space {parent_space.name} is not bookable or not active.")
                return ServiceResult.error_result(
                    message=f"设施 '{target_amenity.amenity.name}' (在 '{parent_space.name}' 中) 不可预订或未启用。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code
                )

            # 追加对象级预订设施权限检查
            if not (user.is_system_admin or user.has_perm('spaces.can_book_amenities_in_space', parent_space)):
                return ServiceResult.error_result(
                    message="您没有权限预订此空间中的设施。",
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

            booking_data['bookable_amenity'] = target_amenity
            booking_data['space'] = parent_space
            requires_approval = parent_space.requires_approval
        else:
            logger.warning("BookingService: Create booking request received without space_id or bookable_amenity_id.")
            return ServiceResult.error_result(
                message="预订必须指定一个空间或可预订设施。",
                error_code=BadRequestException.default_code,
                status_code=BadRequestException.status_code
            )

        booking_data['user'] = user

        # ====== 每日预订次数限制检查 - START (保持不变) ======
        effective_max_daily_bookings = float('inf')

        user_group_ids = list(user.groups.values_list('id', flat=True))

        if user_group_ids:
            active_limits = self.daily_booking_limit_dao.get_active_limits_for_group_ids(user_group_ids)
            for limit_obj in active_limits:
                effective_max_daily_bookings = min(effective_max_daily_bookings, limit_obj.max_bookings)

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

        # ====== 自动化审批逻辑 - START (保持不变) ======
        if not requires_approval and not user.is_system_admin and not user.is_space_manager:  # 自动审批只对普通用户生效，管理员有权选择人工审批
            booking_data['status'] = 'APPROVED'
            booking_data['reviewed_by'] = None
            booking_data['reviewed_at'] = timezone.now()
            message_on_success = "预订已成功批准。"
            logger.info(
                f"BookingService: Booking for user {user.username} of space/amenity {target_space.name if target_space else (target_amenity.amenity.name if target_amenity else 'N/A')} auto-approved.")
        else:
            # 即使 requires_approval=False，管理员创建的也可能是 Pending，以便日后审核
            # 可以根据需要调整，让管理员创建时始终 APPROVED
            booking_data['status'] = 'PENDING'
            message_on_success = "预订请求已提交，等待审核。"
            logger.info(
                f"BookingService: Booking for user {user.username} of space/amenity {target_space.name if target_space else (target_amenity.amenity.name if target_amenity else 'N/A')} submitted for approval.")
        # ====== 自动化审批逻辑 - END ======

        try:
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

    # get_booking: 细粒度权限检查
    def get_booking(self, user: CustomUser, pk: int) -> ServiceResult[Booking]:
        """
        根据ID获取单个预订记录。用户只能查看自己的预订或管理员可查看指定权限预订。
        """
        try:
            booking = self.booking_dao.get_booking_by_id(pk)
            if not booking:
                return ServiceResult.error_result(
                    message="预订未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # 预订者本人始终可以查看
            if booking.user == user:
                return ServiceResult.success_result(data=booking, message="成功获取预订详情。", status_code=200)

            # 非预订者，检查管理员权限
            target_space = self.booking_dao.get_target_space_for_booking(booking)

            # 系统管理员或有全局查看权限
            if user.is_system_admin or user.has_perm('bookings.can_view_all_bookings'):
                return ServiceResult.success_result(data=booking, message="成功获取预订详情。", status_code=200)

            # 空间管理员或被授予查看此空间预订权限的用户
            if target_space and user.has_perm('spaces.can_view_space_bookings', target_space):
                return ServiceResult.success_result(data=booking, message="成功获取预订详情。", status_code=200)

            return ServiceResult.error_result(
                message="您没有权限查看此预订。",
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )
        except Exception as e:
            return self._handle_exception(e, default_message="获取预订详情失败。")

    # get_user_bookings: 保持不变 (只获取当前用户自己的预订)
    def get_user_bookings(self, user: CustomUser) -> QuerySet[Booking]:
        """
        获取用户的所有预订记录。
        """
        return self.booking_dao.get_queryset().filter(user=user)

    # get_all_bookings: 细粒度权限检查
    def get_all_bookings(self, user: CustomUser) -> ServiceResult[QuerySet[Booking]]:
        """
        获取所有预订记录。
        视图层确保用户已认证并通过角色检查 `@is_admin_or_space_manager_required`。
        Service 层在这里进行更细致的数据过滤。
        """
        # 系统管理员或有全局 'can_view_all_bookings' 权限的用户，可以查看所有预订
        if user.is_system_admin or user.has_perm('bookings.can_view_all_bookings'):
            return ServiceResult.success_result(
                data=self.booking_dao.get_queryset(),
                message="成功获取所有预订记录。"
            )
        # 空间管理员或被授权为 SpaceManager 组的用户
        elif user.is_space_manager:  # 只要是空间管理员，就有权限查看其管理的空间的预订
            # 获取用户有 'spaces.can_view_space_bookings' 权限的所有 Space 对象
            managed_spaces_ids = get_objects_for_user(
                user, 'spaces.can_view_space_bookings', klass=Space
            ).values_list('id', flat=True)

            if not managed_spaces_ids:
                return ServiceResult.success_result(
                    data=self.booking_dao.get_queryset().none(),
                    message="您没有权限查看任何管理的预订记录。",
                    status_code=200
                )

            return ServiceResult.success_result(
                data=self.booking_dao.get_queryset().filter(
                    Q(space__id__in=managed_spaces_ids) | Q(bookable_amenity__space__id__in=managed_spaces_ids)
                ).distinct(),
                message="成功获取管理的预订记录。"
            )
        else:  # 为普通用户安全起见，通常 View 层会拦截，这里作为防御性措施
            return ServiceResult.error_result(
                message="您没有权限查看所有（或任何管理的）预订记录。",
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )

    # cancel_booking: 细粒度权限检查
    @transaction.atomic
    def cancel_booking(self, user: CustomUser, booking_id: int) -> ServiceResult[Booking]:
        """
        取消一个预订。视图层确保用户已认证。
        服务层在这里进行对象级权限检查：用户只能取消自己的预订，或有特定管理权限的管理员可以取消。
        """
        booking = self.booking_dao.get_booking_by_id(booking_id)
        if not booking:
            return ServiceResult.error_result(
                message="预订不存在。", error_code=NotFoundException.default_code,
                status_code=NotFoundException.status_code
            )

        # 预订者本人可以取消
        if booking.user == user:
            pass  # 允许取消
        else:  # 非预订者，检查管理权限
            target_space = self.booking_dao.get_target_space_for_booking(booking)
            if not (user.is_system_admin or  # 系统管理员
                    user.has_perm('bookings.can_cancel_any_booking') or  # 全局取消权限
                    (target_space and user.has_perm('spaces.can_cancel_space_bookings', target_space))):  # 对象级取消权限
                return ServiceResult.error_result(
                    message="您没有权限取消此预订。",
                    error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                )

        if booking.status in ['CANCELLED', 'COMPLETED', 'REJECTED', 'NO_SHOW', 'CHECKED_IN', 'CHECKED_OUT']:
            return ServiceResult.error_result(
                message=f"预订 '{booking_id}' 状态为 '{booking.get_status_display()}'，无法取消。",
                error_code=BadRequestException.default_code, status_code=BadRequestException.status_code
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

    # update_booking_status: 细粒度权限检查
    @transaction.atomic
    def update_booking_status(self, user: CustomUser, booking_id: int, new_status: str,
                              admin_notes: Optional[str] = None) -> ServiceResult[Booking]:
        """
        通用更新预订状态的方法（批准、拒绝、签到、签出、未到场、完成）。
        视图层 `@is_admin_or_space_manager_required` 确保了用户角色。
        Service 层在这里进行细粒度对象级权限检查。
        """
        booking = self.booking_dao.get_booking_by_id(booking_id)
        if not booking:
            return ServiceResult.error_result(
                message="预订不存在。", error_code=NotFoundException.default_code,
                status_code=NotFoundException.status_code
            )

        target_space = self.booking_dao.get_target_space_for_booking(booking)

        # Determine which specific permission is needed for the new status
        required_global_perm = None
        required_space_perm = None

        # 权限检查优先级：系统管理员 -> 全局权限 -> 对象级权限
        can_proceed = user.is_system_admin

        if not can_proceed:
            if new_status in ['APPROVED', 'REJECTED']:
                required_global_perm = 'bookings.can_approve_any_booking'
                required_space_perm = 'spaces.can_approve_space_bookings'
            elif new_status in ['CHECKED_IN', 'CHECKED_OUT', 'NO_SHOW']:
                required_global_perm = 'bookings.can_check_in_any_booking'
                required_space_perm = 'spaces.can_checkin_space_bookings'
            elif new_status == 'COMPLETED':
                # 完成状态通常由签出或未到场自动触发，或由管理员手动标记。
                # 手动标记可归类为编辑权限或特定完成权限。
                required_global_perm = 'bookings.can_edit_any_booking_notes'  # 假设可以通过编辑来完成
                required_space_perm = None  # 通常不会有对象级的 'can_complete_space_bookings'，而是通过管理权限来处理
            else:  # Default for unsupported status
                return ServiceResult.error_result(
                    message=f"不支持更新为状态 '{new_status}'。",
                    error_code=BadRequestException.default_code, status_code=BadRequestException.status_code
                )

            if (required_global_perm and user.has_perm(required_global_perm)) or \
                    (target_space and required_space_perm and user.has_perm(required_space_perm, target_space)):
                can_proceed = True

        if not can_proceed:
            error_message = ""
            if new_status in ['APPROVED', 'REJECTED']:
                error_message = "您没有权限批准/拒绝此预订。"
            elif new_status in ['CHECKED_IN', 'CHECKED_OUT', 'NO_SHOW']:
                error_message = "您没有权限签到/签出/标记此预订为未到场。"
            elif new_status == 'COMPLETED':
                error_message = "您没有权限标记此预订为已完成。"
            else:
                error_message = "您没有权限执行此预订状态更新操作。"  # Fallback, should be caught above

            return ServiceResult.error_result(
                message=error_message,
                error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
            )

        # ... (existing business logic / status transition checks remain the same) ...

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