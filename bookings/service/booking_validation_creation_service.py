# bookings/service/booking_validation_creation_service.py
import logging
from typing import Dict, Any, Tuple, Optional, Union
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F, Sum, Q  # 导入 Q 对象进行更复杂的过滤
from django.utils import timezone
from rest_framework import status as http_status  # 导入 HTTP 状态码

from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.service.factory import ServiceFactory  # 用于获取其他 Service 实例
from core.service.cache import CacheService  # 从 core/service/cache.py 导入
from core.utils.exceptions import ServiceException, NotFoundException, BadRequestException, ForbiddenException, \
    ConflictException, InternalServerError
from core.utils import date_utils  # 导入日期工具函数
from bookings.service.common_helpers import CommonBookingHelpers  # 导入通用辅助函数

from users.models import CustomUser
from spaces.models import Space, BookableAmenity, SpaceType
from bookings.models import Booking as BookingModel, BOOKING_STATUS_CHOICES  # Alias Booking model to avoid name clash
from django.contrib.auth.models import Group  # For group-based permissions check and object permissions

logger = logging.getLogger(__name__)


class BookingValidationCreationService(BaseService):
    """
    负责预订的深度校验和最终创建/批准逻辑。
    此服务在 Celery 任务中被调用，采用数据库事务和悲观锁确保数据一致性和并发安全。
    """
    _dao_map = {
        'booking_dao': 'booking',
        'space_dao': 'space',
        'bookable_amenity_dao': 'bookable_amenity',
        'user_penalty_points_dao': 'user_penalty_points',  # 需要获取并锁定用户相关的点数记录
    }

    def __init__(self):
        super().__init__()
        self.booking_dao = self._get_dao_instance('booking')
        self.space_dao = self._get_dao_instance('space')
        self.bookable_amenity_dao = self._get_dao_instance('bookable_amenity')
        self.user_penalty_points_dao = self._get_dao_instance('user_penalty_points')

        # 惰性加载其他 Service 实例
        self._daily_booking_limit_service: Optional['DailyBookingLimitService'] = None
        self._user_ban_service: Optional['UserBanService'] = None
        self._user_exemption_service: Optional['UserExemptionService'] = None

    # Helper functions to get other services
    def _get_daily_booking_limit_service(self):
        if self._daily_booking_limit_service is None:
            self._daily_booking_limit_service = ServiceFactory.get_service('DailyBookingLimitService')
        return self._daily_booking_limit_service

    def _get_user_ban_service(self):
        if self._user_ban_service is None:
            self._user_ban_service = ServiceFactory.get_service('UserBanService')
        return self._user_ban_service

    def _get_user_exemption_service(self):
        if self._user_exemption_service is None:
            self._user_exemption_service = ServiceFactory.get_service('UserExemptionService')
        return self._user_exemption_service

    def deep_validate_and_confirm(self, booking_id: int) -> ServiceResult[BookingModel]:
        """
        对指定 Booking 进行深度业务逻辑校验，并在事务中创建/批准预订。
        如果校验失败，将更新预订状态。

        :param booking_id: 处于 SUBMITTED 状态的 Booking 实例的 ID。
        :return: ServiceResult，成功时 data 包含最终的 Booking 实例。
        """
        booking_instance: Optional[BookingModel] = None
        current_status_message = ""  # 用于admin_notes

        try:
            with transaction.atomic():
                # Step 1: 获取并悲观锁定 Booking 实例
                # select_for_update() 会锁定行直到事务结束，防止其他并发请求修改此行
                booking_instance = self.booking_dao.get_queryset().select_for_update().filter(pk=booking_id).first()

                if not booking_instance:
                    logger.error(f"Deep validation: Booking ID {booking_id} not found for deep validation.")
                    raise NotFoundException(detail="预订记录未找到。")

                # 防止重复处理或处理已失败的请求
                if booking_instance.processing_status not in [
                    BookingModel.PROCESSING_STATUS_CHOICES[0][0],  # 'SUBMITTED'
                    BookingModel.PROCESSING_STATUS_CHOICES[1][0]  # 'IN_PROGRESS'
                ]:
                    logger.info(
                        f"Deep validation: Booking ID {booking_id} is in status {booking_instance.processing_status}, skipping deep validation as it's already processed or failed.")
                    return ServiceResult.success_result(
                        data=booking_instance,
                        message=f"预订 {booking_id} 已经处理过，当前状态为 {booking_instance.get_processing_status_display()}"
                    )

                # 更新处理状态为 IN_PROGRESS
                current_status_message = "预订请求正在进行深层校验。"
                self.booking_dao.update_booking_processing_status(
                    booking_instance,
                    BookingModel.PROCESSING_STATUS_CHOICES[1][0],  # 'IN_PROGRESS'
                    admin_notes=current_status_message
                )
                logger.info(f"Booking ID {booking_id} updated to IN_PROGRESS.")

                # 根据 Booking 目标类型获取并锁定目标对象 (Space 或 BookableAmenity)
                target_obj: Union[Space, BookableAmenity]
                target_space: Space

                if booking_instance.space:
                    target_obj = self.space_dao.get_queryset().select_for_update().get(pk=booking_instance.space_id)
                    target_space = target_obj  # If booking is for space itself
                elif booking_instance.bookable_amenity:
                    target_obj = self.bookable_amenity_dao.get_queryset().select_for_update().get(
                        pk=booking_instance.bookable_amenity_id)
                    target_space = target_obj.space  # Get parent space for the amenity
                else:
                    raise InternalServerError(detail="预订记录无有效目标，数据异常。", code="invalid_booking_target")

                # 再次获取 space_type,确保是最新的，因为是外键，它会自动更新到 latest
                target_space_type: Optional[SpaceType] = target_space.space_type

                # 锁定用户相关的违约点数记录 (如果存在的话), 确保在后续流程中不会被并发修改
                # 如果记录不存在（用户是首次违规），则不需要锁定
                user_penalty_points_record = self.user_penalty_points_dao.get_queryset().select_for_update().filter(
                    user=booking_instance.user,
                    space_type=target_space_type
                ).first()
                # user_penalty_points_record is None means no record exists, will create if needed

                # Step 2: 重新执行所有业务逻辑校验 (基于锁定的最新数据)

                # 2.1 目标自身状态校验 (最新数据)
                if isinstance(target_obj, Space):
                    if target_obj.is_container:
                        raise BadRequestException(detail=f"空间 '{target_obj.name}' 是一个容器，不能直接预订。",
                                                  code="cannot_book_container_space")
                    if not target_obj.is_bookable:
                        raise BadRequestException(detail=f"空间 '{target_obj.name}' 当前不可预订。",
                                                  code="space_not_bookable_locked")
                    if not target_obj.is_active:
                        raise BadRequestException(detail=f"空间 '{target_obj.name}' 当前不活跃。",
                                                  code="space_not_active_locked")
                    # 对于空间预订，quantity 总是 1
                    if booking_instance.booked_quantity != 1:
                        raise BadRequestException(detail="预订整个空间时，数量必须为1。",
                                                  code="invalid_space_booking_quantity_locked")

                    target_capacity = target_obj.capacity  # 空间的容量

                elif isinstance(target_obj, BookableAmenity):
                    if not target_obj.is_bookable:
                        raise BadRequestException(detail=f"设施 '{target_obj.amenity.name}' 当前不可预订。",
                                                  code="amenity_not_bookable_locked")
                    if not target_obj.is_active:
                        raise BadRequestException(detail=f"设施 '{target_obj.amenity.name}' 当前不活跃。",
                                                  code="amenity_not_active_locked")
                    if booking_instance.booked_quantity > target_obj.quantity:
                        raise BadRequestException(
                            detail=f"预订数量 {booking_instance.booked_quantity} 超过设施总数量 {target_obj.quantity}。",
                            code="exceeds_amenity_capacity_locked")
                    target_capacity = target_obj.quantity  # 设施的容量/总数

                else:
                    raise InternalServerError(detail="未知预订目标类型。", code="unknown_target_type")

                # 获取有效的预订规则 (从 Space 或 SpaceType 继承)
                # 这些规则是 Space 上的字段，会根据 SpaceType 默认值进行填充
                effective_min_duration = target_space.min_booking_duration or \
                                         (target_space_type.default_min_booking_duration if target_space_type else None)
                effective_max_duration = target_space.max_booking_duration or \
                                         (target_space_type.default_max_booking_duration if target_space_type else None)
                effective_available_start_time = target_space.available_start_time or \
                                                 (
                                                     target_space_type.default_available_start_time if target_space_type else None)
                effective_available_end_time = target_space.available_end_time or \
                                               (
                                                   target_space_type.default_available_end_time if target_space_type else None)
                effective_buffer_time_minutes = target_space.buffer_time_minutes if target_space.buffer_time_minutes is not None else \
                    (target_space_type.default_buffer_time_minutes if target_space_type else 0)

                # 2.2 时间校验 (重做，确保基于最新数据)
                try:
                    date_utils.validate_booking_time_integrity(booking_instance.start_time, booking_instance.end_time)
                    date_utils.validate_booking_duration(booking_instance.start_time, booking_instance.end_time,
                                                         effective_min_duration, effective_max_duration)
                    date_utils.validate_booking_daily_availability(booking_instance.start_time,
                                                                   booking_instance.end_time,
                                                                   effective_available_start_time,
                                                                   effective_available_end_time)
                except Exception as e:
                    error_detail = e.messages[0] if hasattr(e, 'messages') else str(e)
                    raise BadRequestException(detail=error_detail, code="invalid_booking_time_locked")

                # 2.3 精细权限检查 (确保用户有权预订此目标)
                # 只有系统管理员或超级用户预订不受限，其他用户需要检查组权限或 is_basic_infrastructure
                if not (booking_instance.user.is_superuser or booking_instance.user.is_system_admin):
                    # 如果用户是空间管理员，但预订的目标不是自己管理的，则需要进一步检查
                    # 对于普通用户，检查他们是否有权预订：
                    # 1. 如果空间是基础型基础设施，通常所有认证用户都可预订（前提是空间本身is_bookable）
                    if target_space_type and target_space_type.is_basic_infrastructure:
                        pass
                        # 2. 检查用户是否在空间的 `permitted_groups` 中
                    elif target_space.permitted_groups.filter(pk__in=booking_instance.user.groups.all()).exists():
                        pass
                    # 3. 检查用户是否显式拥有对象级权限 (例如 `can_book_this_space` 或 `can_book_amenities_in_space`)
                    #    这里为了简化，更倾向于使用上述两种方式，如果需要更细粒度的对象级权限检查，可以整合guardian的 `has_perm`
                    #    暂时假设 SpaceService.get_space_by_id 和上面的 permitted_groups 检查已经足够。
                    else:
                        raise ForbiddenException(detail="您没有权限预订此空间/设施。",
                                                 code="user_unauthorized_to_book_locked")

                # 2.4 资源冲突检测 (核心并发控制)
                # 查找与当前预订时间段重叠的所有非自身、活跃状态的预订
                overlapping_bookings_qs = self.booking_dao.get_overlapping_bookings(
                    target_entity=target_obj,
                    start_time=booking_instance.start_time,
                    end_time=booking_instance.end_time,
                    exclude_booking_id=booking_instance.pk if booking_instance.pk else None
                ).select_for_update()  # 对这些冲突预订也进行锁定，防止它们被修改

                # 收集冲突预订数据，以便 CommonBookingHelpers 处理
                booked_slots = [
                    {'start_time': b.start_time, 'end_time': b.end_time, 'booked_quantity': b.booked_quantity}
                    for b in overlapping_bookings_qs
                ]

                is_available = CommonBookingHelpers.is_time_slot_available(
                    booked_slots=booked_slots,
                    new_start=booking_instance.start_time,
                    new_end=booking_instance.end_time,
                    booked_quantity=booking_instance.booked_quantity,
                    total_capacity=target_capacity,
                    buffer_time_minutes=effective_buffer_time_minutes
                )

                if not is_available:
                    raise ConflictException(detail="预订时间段与现有预订冲突或资源容量不足。",
                                            code="booking_time_conflict_locked")

                # 2.5 用户禁用/豁免 (再次精确检查)
                user_ban_service = self._get_user_ban_service()
                is_banned_result = user_ban_service.is_user_banned(booking_instance.user, target_space_type)
                if not is_banned_result.success:
                    logger.error(
                        f"Failed to perform deep check for user ban status for user {booking_instance.user.pk}: {is_banned_result.errors}")
                    raise ServiceException(message="深层检查用户禁用状态失败。", errors=is_banned_result.errors,
                                           status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

                if is_banned_result.data:
                    user_exemption_service = self._get_user_exemption_service()
                    is_exempted_result = user_exemption_service.is_user_exempted(booking_instance.user,
                                                                                 target_space_type)
                    if not is_exempted_result.success:
                        logger.error(
                            f"Failed to perform deep check for user exemption status for user {booking_instance.user.pk}: {is_exempted_result.errors}")
                        raise ServiceException(message="深层检查用户豁免状态失败。", errors=is_exempted_result.errors,
                                               status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

                    if not is_exempted_result.data:
                        raise ForbiddenException(
                            message=f"用户在 {target_space_type.name if target_space_type else '全局'} 空间类型下被禁用，无法预订。",
                            code="user_banned_locked")

                # 2.6 每日预订限制 (再次精确计算)
                daily_limit_service = self._get_daily_booking_limit_service()
                effective_limit = daily_limit_service.get_effective_daily_limit(booking_instance.user,
                                                                                target_space_type)

                if effective_limit > 0:
                    today = booking_instance.start_time.date()
                    # 重新计算当前用户在今天的总预订数，这包括所有已批准、待审批、已签到的预订
                    current_bookings_count = self.booking_dao.get_user_bookings_count_for_date(
                        user=booking_instance.user,
                        date=today,
                        status_in=[BookingModel.BOOKING_STATUS_CHOICES[0][0],  # PENDING
                                   BookingModel.BOOKING_STATUS_CHOICES[1][0],  # APPROVED
                                   BookingModel.BOOKING_STATUS_CHOICES[6][0]]  # CHECKED_IN
                    )

                    # 加上当前预订请求的数量。
                    # 这里假设`effective_limit`是按“预订次数”计算，而不是“预订资源总和”
                    if current_bookings_count + 1 > effective_limit:  # 当前预订会使得总次数超限
                        raise ForbiddenException(
                            message=f"您在 {target_space_type.name if target_space_type else '全局'} 空间类型下，当日已达最大预订次数限制 ({effective_limit}次)。",
                            error_code="daily_booking_limit_exceeded_locked",
                            status_code=http_status.HTTP_403_FORBIDDEN
                        )
                # 至此，所有校验通过

                # Step 3: 更新 Booking 最终状态
                booking_instance.processing_status = BookingModel.PROCESSING_STATUS_CHOICES[2][0]  # 'CREATED'

                # 确定最终的业务状态
                final_booking_status = BookingModel.BOOKING_STATUS_CHOICES[1][0]  # 默认为 'APPROVED'

                # 如果目标空间/设施需要审批
                if target_space.requires_approval:
                    final_booking_status = BookingModel.BOOKING_STATUS_CHOICES[0][0]  # 'PENDING'

                # (可选：管理员绕过审批逻辑)
                # 如果用户是系统管理员或空间管理员，可以跳过审批直接批准 (即使 requires_approval=True)
                # 但这里目前的实现是：如果空间配置需要审批，即使管理员创建/提交，也先挂起。
                # 管理员可以在后续的审批流程中手动批准。

                booking_instance.status = final_booking_status
                booking_instance.admin_notes = "深层校验通过，预订已创建。"
                if final_booking_status == BookingModel.BOOKING_STATUS_CHOICES[0][0]:  # 如果是 PENDING
                    booking_instance.admin_notes += "等待管理员审批。"

                self.booking_dao.update_booking(booking_instance)  # 使用 DAO 更新
                logger.info(
                    f"Deep validation successful and booking ID {booking_id} confirmed to {booking_instance.status} status.")
                return ServiceResult.success_result(
                    data=booking_instance,
                    message="预订已成功创建。",
                    status_code=http_status.HTTP_201_CREATED
                )

        except ServiceException as e:
            logger.warning(f"Deep validation failed (ServiceException) for booking ID {booking_id}: {e.message}")
            if booking_instance:
                # 记录失败状态和原因
                current_status_message = f"深层校验失败: {e.message} ({e.error_code})"
                self.booking_dao.update_booking_processing_status(
                    booking_instance,
                    BookingModel.PROCESSING_STATUS_CHOICES[3][0],  # FAILED_VALIDATION
                    admin_notes=current_status_message,
                    new_booking_status=BookingModel.BOOKING_STATUS_CHOICES[2][0]  # REJECTED
                )
            return self._handle_exception(e)  # re-raise the exception for _handle_exception

        except NotFoundException as e:
            current_status_message = f"深层校验失败: {e.detail}"
            logger.warning(f"Deep validation failed (NotFoundException) for booking ID {booking_id}: {e.detail}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_instance,
                    BookingModel.PROCESSING_STATUS_CHOICES[3][0],  # FAILED_VALIDATION
                    admin_notes=current_status_message,
                    new_booking_status=BookingModel.BOOKING_STATUS_CHOICES[2][0]  # REJECTED
                )
            return self._handle_exception(e)

        except BadRequestException as e:
            current_status_message = f"深层校验失败: {e.detail}"
            logger.warning(f"Deep validation failed (BadRequestException) for booking ID {booking_id}: {e.detail}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_instance,
                    BookingModel.PROCESSING_STATUS_CHOICES[3][0],  # FAILED_VALIDATION
                    admin_notes=current_status_message,
                    new_booking_status=BookingModel.BOOKING_STATUS_CHOICES[2][0]  # REJECTED
                )
            return self._handle_exception(e)

        except ForbiddenException as e:
            current_status_message = f"深层校验失败: {e.detail}"
            logger.warning(f"Deep validation failed (ForbiddenException) for booking ID {booking_id}: {e.detail}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_instance,
                    BookingModel.PROCESSING_STATUS_CHOICES[3][0],  # FAILED_VALIDATION
                    admin_notes=current_status_message,
                    new_booking_status=BookingModel.BOOKING_STATUS_CHOICES[2][0]  # REJECTED
                )
            return self._handle_exception(e)

        except ConflictException as e:
            current_status_message = f"深层校验失败: {e.detail}"
            logger.warning(f"Deep validation failed (ConflictException) for booking ID {booking_id}: {e.detail}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_instance,
                    BookingModel.PROCESSING_STATUS_CHOICES[3][0],  # FAILED_VALIDATION
                    admin_notes=current_status_message,
                    new_booking_status=BookingModel.BOOKING_STATUS_CHOICES[2][0]  # REJECTED
                )
            return self._handle_exception(e)

        except Exception as e:
            current_status_message = f"深层校验运行时错误: {str(e)}"
            logger.exception(f"Unhandled error during deep booking validation for booking ID {booking_id}: {e}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_instance,
                    BookingModel.PROCESSING_STATUS_CHOICES[4][0],  # FAILED_RUNTIME
                    admin_notes=current_status_message,
                    new_booking_status=BookingModel.BOOKING_STATUS_CHOICES[2][0]  # REJECTED
                )
            return self._handle_exception(e, default_code="deep_validation_runtime_error")