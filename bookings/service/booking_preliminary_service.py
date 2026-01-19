# bookings/service/booking_preliminary_service.py
import logging
from typing import Dict, Any, Tuple, Optional
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from rest_framework import status as http_status  # 导入 HTTP 状态码

from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.service.factory import ServiceFactory  # 用于获取其他 Service 实例
from core.utils.exceptions import ServiceException, NotFoundException, BadRequestException, InternalServerError
from core.utils import date_utils  # 导入日期工具函数

# 导入模型，用于类型提示和 ServiceResult 返回数据
from users.models import CustomUser
from spaces.models import Space, BookableAmenity, SpaceType
from bookings.models import Booking as BookingModel  # 避免与 Service 类名冲突，使用别名

# 导入异步任务，以便在初步校验成功后触发
from bookings.tasks import booking_tasks  # 将在 Task 2.6 中创建

logger = logging.getLogger(__name__)


class BookingPreliminaryService(BaseService):
    """
    负责对预订请求进行初步（轻量级）校验。
    这些校验不涉及事务锁定，适合在接收到用户请求时快速返回结果，
    减少不必要的深层处理和并发竞争。
    成功通过初步校验后，异步任务将被触发进行深层校验和实际预订创建。
    """
    _dao_map = {
        'booking_dao': 'booking',
        'space_dao': 'space',  # 需要 space dao 来获取目标空间信息
        'bookable_amenity_dao': 'bookable_amenity',  # 需要 bookable amenity dao 来获取目标设施信息
    }

    def __init__(self):
        super().__init__()
        self.booking_dao = self._get_dao_instance('booking')
        self.space_dao = self._get_dao_instance('space')
        self.bookable_amenity_dao = self._get_dao_instance('bookable_amenity')

        # 惰性加载其他 Service 实例
        self._daily_booking_limit_service = None
        self._user_ban_service = None
        self._user_exemption_service = None

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

    def pre_validate(self, user: CustomUser, request_data: Dict[str, Any]) -> ServiceResult[
        Tuple[int, Optional[Space], Optional[BookableAmenity]]]:
        """
        对预订请求进行初步校验。

        :param user: 当前请求的用户。
        :param request_data: 包含预订详情的字典，如 `request_uuid`, `space_id` 或 `bookable_amenity_id`,
                             `start_time`, `end_time`, `booked_quantity`, `purpose`, `expected_attendees`。
        :return: ServiceResult，成功时 data 包含 tuple (booking_id, target_space, target_amenity)。
                 如果因幂等性直接返回成功，data 包含 (existing_booking_id, None, None)。
        """
        try:
            request_uuid = request_data.get('request_uuid')
            if not request_uuid:
                raise BadRequestException(detail="请求唯一标识 (request_uuid) 不能为空。", code="missing_request_uuid")

            # 1. 幂等性初检
            existing_booking = self.booking_dao.get_booking_by_request_uuid(request_uuid)
            if existing_booking:
                if existing_booking.processing_status != 'FAILED_VALIDATION':
                    logger.info(
                        f"Idempotency check: Request UUID {request_uuid} already processed or in progress (status: {existing_booking.processing_status}). Returning existing booking ID.")
                    return ServiceResult.success_result(
                        data=(existing_booking.pk, None, None),  # 返回现有预订ID，后续ServiceResult的处理可以利用它
                        message="请求已在处理中或已完成。",
                        status_code=http_status.HTTP_200_OK  # 幂等性请求通常返回 200 OK
                    )
                else:
                    logger.warning(
                        f"Idempotency check: Request UUID {request_uuid} processed but failed validation. Returning conflict.")
                    return ServiceResult.error_result(
                        message="此请求 UUID 已处理但验证失败，请使用新的 UUID 重新提交。",
                        errors=[existing_booking.admin_notes],
                        error_code="request_uuid_failed_previous_validation",
                        status_code=http_status.HTTP_409_CONFLICT
                    )

            # 2. 目标存在性与基本可用性
            space_id = request_data.get('space_id')
            bookable_amenity_id = request_data.get('bookable_amenity_id')

            target_space: Optional[Space] = None
            target_amenity: Optional[BookableAmenity] = None
            target_space_type: Optional[SpaceType] = None

            if space_id and bookable_amenity_id:
                raise BadRequestException(detail="预订必须且只能指定一个目标：空间或设施实例。",
                                          code="ambiguous_booking_target")
            elif space_id:
                space_result = self.space_dao.get_space_by_id(user, space_id)  # 这里的user参数是为了权限过滤
                if not space_result:
                    raise NotFoundException(detail=f"预订目标空间 (ID: {space_id}) 未找到或您无权查看。",
                                            code="space_not_found_or_unauthorized")
                target_space = space_result
                target_space_type = target_space.space_type
                if target_space.is_container:
                    raise BadRequestException(detail=f"空间 '{target_space.name}' 是一个容器，不能直接预订。",
                                              code="cannot_book_container_space")
                if not target_space.is_bookable:
                    raise BadRequestException(detail=f"空间 '{target_space.name}' 当前不可预订。",
                                              code="space_not_bookable")
                if not target_space.is_active:
                    raise BadRequestException(detail=f"空间 '{target_space.name}' 当前不活跃。", code="space_not_active")

                # 检查预订数量
                if request_data.get('booked_quantity', 1) != 1:
                    raise BadRequestException(detail="预订整个空间时，数量必须为1。",
                                              code="invalid_space_booking_quantity")

            elif bookable_amenity_id:
                amenity_result = self.bookable_amenity_dao.get_bookable_amenity_by_id(bookable_amenity_id)
                if not amenity_result:
                    raise NotFoundException(detail=f"预订目标设施实例 (ID: {bookable_amenity_id}) 未找到。",
                                            code="bookable_amenity_not_found")
                target_amenity = amenity_result
                target_space = target_amenity.space  # 获取设施所在的父空间
                target_space_type = target_space.space_type

                if not target_amenity.is_bookable:
                    raise BadRequestException(
                        detail=f"设施实例 '{target_amenity.amenity.name}' (ID: {bookable_amenity_id}) 当前不可预订。",
                        code="amenity_not_bookable")
                if not target_amenity.is_active:
                    raise BadRequestException(
                        detail=f"设施实例 '{target_amenity.amenity.name}' (ID: {bookable_amenity_id}) 当前不活跃。",
                        code="amenity_not_active")

                # 检查预订数量是否超过可用库存
                booked_quantity = request_data.get('booked_quantity', 1)
                if booked_quantity <= 0:
                    raise BadRequestException(detail="预订数量必须大于0。", code="invalid_booking_quantity")
                if target_amenity.quantity is not None and booked_quantity > target_amenity.quantity:
                    raise BadRequestException(detail=f"预订数量不能超过设施总数量 {target_amenity.quantity}。",
                                              code="exceeds_amenity_capacity")
            else:
                raise BadRequestException(detail="预订必须指定空间ID或设施实例ID。", code="missing_booking_target")

            # 从目标对象获取SpaceType的最终确认 (如果TargetSpace已确定)
            # target_space 此时必定已确定 (无论是直接预订空间还是通过设施获取父空间)
            if not target_space_type and target_space:
                target_space_type = target_space.space_type

            if not target_space:  # 理论上不会发生，因为上面的逻辑确保了
                raise InternalServerError(detail="无法确定预订目标所属的空间，系统内部错误。",
                                          code='missing_related_space_internal')

            # 3. 基础时间校验
            start_time_str = request_data.get('start_time')
            end_time_str = request_data.get('end_time')
            if not start_time_str or not end_time_str:
                raise BadRequestException(detail="预订的开始时间和结束时间不能为空。", code="missing_time_data")

            # 将字符串日期时间转换为 timezone-aware datetime 对象
            try:
                start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
                end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
            except ValueError:
                raise BadRequestException(
                    detail="开始时间或结束时间格式无效，请使用 ISO 8601 格式 (例如: '2023-10-27T10:00:00+08:00')。",
                    code="invalid_datetime_format")

            # 调用 date_utils 进行时间完整性校验
            # 注意：date_utils.validate_booking_time_integrity 自己会抛出 ValidationError，我们需捕获并转换
            try:
                date_utils.validate_booking_time_integrity(start_time, end_time)
            except Exception as e:  # 捕获ValidationError或ServiceException
                # Convert Django ValidationError to ServiceException or CustomAPIException
                error_detail = e.messages[0] if hasattr(e, 'messages') else str(e)  # e.g. from ValidationError
                logger.warning(f"Basic time integrity validation failed: {error_detail}")
                raise BadRequestException(detail=error_detail, code="invalid_booking_time")

            # 使用目标空间的有效规则进行时长和每日可用时间校验
            effective_min_duration = target_space.min_booking_duration or \
                                     (target_space_type.default_min_booking_duration if target_space_type else None)
            effective_max_duration = target_space.max_booking_duration or \
                                     (target_space_type.default_max_booking_duration if target_space_type else None)
            effective_available_start_time = target_space.available_start_time or \
                                             (
                                                 target_space_type.default_available_start_time if target_space_type else None)
            effective_available_end_time = target_space.available_end_time or \
                                           (target_space_type.default_available_end_time if target_space_type else None)

            try:
                date_utils.validate_booking_duration(start_time, end_time, effective_min_duration,
                                                     effective_max_duration)
                date_utils.validate_booking_daily_availability(start_time, end_time, effective_available_start_time,
                                                               effective_available_end_time)
            except Exception as e:
                error_detail = e.messages[0] if hasattr(e, 'messages') else str(e)
                logger.warning(f"Booking duration or daily availability validation failed: {error_detail}")
                raise BadRequestException(detail=error_detail, code="booking_duration_or_availability_invalid")

            # 4. 用户禁用/豁免初筛
            user_ban_service = self._get_user_ban_service()
            is_banned_result = user_ban_service.is_user_banned(user, target_space_type)
            if not is_banned_result.success:
                logger.error(f"Failed to check user ban status for user {user.pk}: {is_banned_result.errors}")
                raise ServiceException(message="检查用户禁用状态失败。", errors=is_banned_result.errors,
                                       status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

            if is_banned_result.data:
                # 如果用户被禁用，进一步检查是否有豁免
                user_exemption_service = self._get_user_exemption_service()
                is_exempted_result = user_exemption_service.is_user_exempted(user, target_space_type)
                if not is_exempted_result.success:
                    logger.error(
                        f"Failed to check user exemption status for user {user.pk}: {is_exempted_result.errors}")
                    raise ServiceException(message="检查用户豁免状态失败。", errors=is_exempted_result.errors,
                                           status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

                if not is_exempted_result.data:
                    raise ServiceException(
                        message=f"用户在 {target_space_type.name if target_space_type else '全局'} 空间类型下被禁用，无法预订。",
                        error_code="user_banned",
                        status_code=http_status.HTTP_403_FORBIDDEN  # 403 Forbidden
                    )

            # 5. 每日预订限制初筛 (不锁定)
            daily_limit_service = self._get_daily_booking_limit_service()
            effective_limit = daily_limit_service.get_effective_daily_limit(user, target_space_type)

            if effective_limit > 0:  # 0 表示不限制
                today = start_time.date()  # 注意这里是 start_time 的日期
                # 状态只计算 PENDING, APPROVED, CHECKED_IN 的数量
                current_bookings_count = self.booking_dao.get_user_bookings_count_for_date(
                    user=user,
                    date=today,
                    status_in=[BookingModel.BOOKING_STATUS_CHOICES[0][0],  # PENDING
                               BookingModel.BOOKING_STATUS_CHOICES[1][0],  # APPROVED
                               BookingModel.BOOKING_STATUS_CHOICES[6][0]]  # CHECKED_IN
                )

                if current_bookings_count >= effective_limit:
                    raise ServiceException(
                        message=f"您在 {target_space_type.name if target_space_type else '全局'} 空间类型下，当日已达最大预订次数限制 ({effective_limit}次)。",
                        error_code="daily_booking_limit_exceeded",
                        status_code=http_status.HTTP_403_FORBIDDEN  # 403 Forbidden
                    )

            # 6. 初步校验通过，创建 BookingModel 实例并设置为 SUBMITTED 状态，然后触发异步任务
            # 这里只用于记录初始请求，不进行最终确认。
            # 大部分字段直接从 request_data 传入

            # 确保 required_data 中包含所有 Booking 模型创建所需的字段
            # 注意：Booking模型需要 space 或 bookable_amenity，不能同时为空，也不能同时存在
            required_booking_fields = {
                'user': user,
                'request_uuid': request_uuid,
                'start_time': start_time,
                'end_time': end_time,
                'booked_quantity': request_data.get('booked_quantity', 1),
                'purpose': request_data.get('purpose', ''),
            }

            if target_space and not target_amenity:  # 仅当直接预订空间时
                required_booking_fields['space'] = target_space
            elif target_amenity:  # 仅当预订设施实例时
                required_booking_fields['bookable_amenity'] = target_amenity

            # 预期参与人数 (Optional)
            if 'expected_attendees' in request_data:
                required_booking_fields['expected_attendees'] = request_data['expected_attendees']

            # 使用 transaction.atomic() 确保创建 initial booking 是原子操作
            with transaction.atomic():
                # 创建一个处于 SUBMITTED 状态的 Booking 记录
                initial_booking_instance = self.booking_dao.create_booking(
                    status=BookingModel.BOOKING_STATUS_CHOICES[0][0],  # 'PENDING'
                    processing_status=BookingModel.PROCESSING_STATUS_CHOICES[0][0],  # 'SUBMITTED'
                    **required_booking_fields
                )

            # 触发异步深度校验任务
            # booking_tasks 将会在 Task 2.6 中定义
            booking_tasks.process_booking_creation_task.delay(initial_booking_instance.pk)

            logger.info(
                f"Preliminary validation successful for request_uuid {request_uuid}. Booking ID {initial_booking_instance.pk} created in SUBMITTED state, deep validation task dispatched.")
            return ServiceResult.success_result(
                data=(initial_booking_instance.pk, target_space, target_amenity),
                message="预订请求已初步验证并提交进行深层处理。",
                status_code=http_status.HTTP_202_ACCEPTED  # 202 Accepted 表示请求已接受但未完成处理
            )

        except ServiceException as e:
            logger.warning(f"Preliminary validation failed (ServiceException): {e.message}")
            return self._handle_exception(e)  # ServiceException 会被 _handle_exception 转换

        except NotFoundException as e:
            logger.warning(f"Preliminary validation failed (NotFoundException): {e.detail}")
            return self._handle_exception(e)

        except BadRequestException as e:
            logger.warning(f"Preliminary validation failed (BadRequestException): {e.detail}")
            return self._handle_exception(e)

        except Exception as e:
            logger.exception(
                f"Unhandled error during preliminary booking validation for user {user.pk}, request_uuid {request_data.get('request_uuid')}.")
            return self._handle_exception(e, default_message="初步预订验证失败，发生未知错误。")