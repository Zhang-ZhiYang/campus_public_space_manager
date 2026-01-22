import logging
from typing import Dict, Any, Tuple, Optional, Union
from datetime import datetime, date, timedelta # 导入 timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status as http_status

from bookings.tasks import booking_tasks
from core.dao import DAOFactory
from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.service.factory import ServiceFactory
from core.utils.exceptions import ServiceException, NotFoundException, BadRequestException, InternalServerError, \
    ConflictException, ForbiddenException, CustomAPIException
from core.utils import date_utils
from bookings.service.common_helpers import CommonBookingHelpers

from users.models import CustomUser
from spaces.models import Space, BookableAmenity, SpaceType
from bookings.models import Booking

logger = logging.getLogger(__name__)

class BookingPreliminaryService(BaseService):
    _dao_map = {
        'booking_dao': 'booking',
        'space_dao': 'space',
        'bookable_amenity_dao': 'bookable_amenity',
    }

    def __init__(self):
        super().__init__()
        logger.debug("BookingPreliminaryService: Initializing DAOs...")
        self.booking_dao = DAOFactory.get_dao('booking')
        self.space_dao = DAOFactory.get_dao('space')
        self.bookable_amenity_dao = DAOFactory.get_dao('bookable_amenity')
        logger.debug(
            f"BookingPreliminaryService: DAOs initialized. booking_dao: {self.booking_dao}, space_dao: {self.space_dao}, bookable_amenity_dao: {self.bookable_amenity_dao}")

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

    def pre_validate(self, user: CustomUser, request_data: Dict[str, Any]) -> ServiceResult[Dict[str, Any]]:
        logger.debug(
            f"BookingPreliminaryService: Entering pre_validate for user {user.pk} with request_data: {request_data}")
        try:
            request_uuid = request_data.get('request_uuid')
            if not request_uuid:
                raise BadRequestException(detail="请求唯一标识 (request_uuid) 不能为空。", code="missing_request_uuid")

            existing_booking = self.booking_dao.get_booking_by_request_uuid(request_uuid)
            if existing_booking:
                if existing_booking.processing_status != Booking.PROCESSING_STATUS_FAILED_VALIDATION:
                    logger.info(
                        f"Idempotency check: Request UUID {request_uuid} already processed or in progress (status: {existing_booking.processing_status}). Returning 200 OK.")
                    return ServiceResult.success_result(
                        data={
                            'booking_id': existing_booking.pk,
                            'request_uuid': str(existing_booking.request_uuid),
                        },
                        message="请求已在处理中或已完成。",
                        status_code=http_status.HTTP_200_OK
                    )
                else:
                    logger.warning(
                        f"Idempotency check: Request UUID {request_uuid} processed but failed validation. Returning conflict.")
                    raise ServiceException(
                        message="此请求 UUID 已处理但验证失败，请使用新的 UUID 重新提交。",
                        errors=[existing_booking.admin_notes],
                        error_code="request_uuid_failed_previous_validation",
                        status_code=http_status.HTTP_409_CONFLICT
                    )

            space_id = request_data.get('space_id')
            bookable_amenity_id = request_data.get('bookable_amenity_id')

            target_obj: Optional[Union[Space, BookableAmenity]] = None
            target_space: Optional[Space] = None
            target_space_type: Optional[SpaceType] = None

            if space_id and bookable_amenity_id:
                raise BadRequestException(detail="预订必须且只能指定一个目标：空间或设施实例。",
                                          code="ambiguous_booking_target")
            elif space_id:
                space_obj = self.space_dao.get_space_by_id(user, space_id)
                if not space_obj:
                    raise NotFoundException(detail=f"预订目标空间 (ID: {space_id}) 未找到或您无权查看。",
                                            code="space_not_found_or_unauthorized")
                target_obj = space_obj
                target_space = target_obj
                target_space_type = target_space.space_type
                if target_space.is_container:
                    raise BadRequestException(detail=f"空间 '{target_space.name}' 是一个容器，不能直接预订。",
                                              code="cannot_book_container_space")
                if not target_space.is_bookable:
                    raise BadRequestException(detail=f"空间 '{target_space.name}' 当前不可预订。",
                                              code="space_not_bookable")
                if not target_space.is_active:
                    raise BadRequestException(detail=f"空间 '{target_space.name}' 当前不活跃。", code="space_not_active")
                if request_data.get('booked_quantity', 1) != 1:
                    raise BadRequestException(detail="预订整个空间时，数量必须为1。",
                                              code="invalid_space_booking_quantity")
            elif bookable_amenity_id:
                amenity_obj = self.bookable_amenity_dao.get_bookable_amenity_by_id(bookable_amenity_id)
                if not amenity_obj:
                    raise NotFoundException(detail=f"预订目标设施实例 (ID: {bookable_amenity_id}) 未找到。",
                                            code="bookable_amenity_not_found")
                target_obj = amenity_obj
                target_space = target_obj.space
                target_space_type = target_space.space_type
                if not target_obj.is_bookable:
                    raise BadRequestException(
                        detail=f"设施实例 '{target_obj.amenity.name}' (ID: {bookable_amenity_id}) 当前不可预订。",
                        code="amenity_not_bookable")
                if not target_obj.is_active:
                    raise BadRequestException(
                        detail=f"设施实例 '{target_obj.amenity.name}' (ID: {bookable_amenity_id}) 当前不活跃。",
                        code="amenity_not_active")
                booked_quantity = request_data.get('booked_quantity', 1)
                if booked_quantity <= 0:
                    raise BadRequestException(detail="预订数量必须大于0。", code="invalid_booking_quantity")
                # Removed the simple 'booked_quantity > target_obj.quantity' check here for BookableAmenity
                # as it's now covered by the is_time_slot_available with total_capacity.
                # However, for a direct "new booked quantity exceeds total amenity quantity" we can still keep it
                # Re-adding this explicit check makes it clearer and handles simple cases earlier.
                if target_obj.quantity is not None and booked_quantity > target_obj.quantity:
                    raise BadRequestException(detail=f"预订数量 {booked_quantity} 不能超过设施总数量 {target_obj.quantity}。",
                                              code="exceeds_amenity_capacity")

            else:
                raise BadRequestException(detail="预订必须指定空间ID或设施实例ID。", code="missing_booking_target")

            if not target_space:
                raise InternalServerError(detail="无法确定预订目标所属的空间，系统内部错误。",
                                          code='missing_related_space_internal')
            if not target_space_type:
                target_space_type = target_space.space_type

            start_time_input = request_data.get('start_time')
            end_time_input = request_data.get('end_time')
            logger.debug(f"Time parsing: start_time_input type {type(start_time_input)} value {start_time_input}")
            logger.debug(f"Time parsing: end_time_input type {type(end_time_input)} value {end_time_input}")

            if not start_time_input or not end_time_input:
                raise BadRequestException(detail="预订的开始时间和结束时间不能为空。", code="missing_time_data")

            try:
                start_time: datetime
                end_time: datetime

                if isinstance(start_time_input, datetime):
                    start_time = start_time_input
                else:
                    start_time = datetime.fromisoformat(str(start_time_input))
                    if timezone.is_naive(start_time):
                        start_time = timezone.make_aware(start_time, timezone.get_current_timezone())

                if isinstance(end_time_input, datetime):
                    end_time = end_time_input
                else:
                    end_time = datetime.fromisoformat(str(end_time_input))
                    if timezone.is_naive(end_time):
                        end_time = timezone.make_aware(end_time, timezone.get_current_timezone())

                logger.debug(
                    f"Successfully parsed start_time: {start_time} (aware: {timezone.is_aware(start_time)}), end_time: {end_time} (aware: {timezone.is_aware(end_time)})")

            except ValueError as e:
                logger.error(
                    f"ValueError during datetime parsing: {e}. Raw data: start={start_time_input}, end={end_time_input}",
                    exc_info=True)
                raise BadRequestException(
                    detail="开始时间或结束时间格式无效，请使用 ISO 8601 格式 (例如: '2023-10-27T10:00:00+08:00')。",
                    code="invalid_datetime_format")

            # --- 时长和可用性验证 ---
            effective_min_duration = target_space.min_booking_duration or \
                                     (target_space_type.default_min_booking_duration if target_space_type else None)
            effective_max_duration = target_space.max_booking_duration or \
                                     (target_space_type.default_max_booking_duration if target_space_type else None)
            effective_available_start_time = target_space.available_start_time or \
                                             (
                                                 target_space_type.default_available_start_time if target_space_type else None)
            effective_available_end_time = target_space.available_end_time or \
                                           (target_space_type.default_available_end_time if target_space_type else None)
            effective_buffer_time_minutes = target_space.buffer_time_minutes if target_space.buffer_time_minutes is not None else \
                (target_space_type.default_buffer_time_minutes if target_space_type else 0)
            # 修正日志级别，因为之前误用 error
            logger.debug(f"Resolved effective_buffer_time_minutes: {effective_buffer_time_minutes} "
                         f"(from Space: {target_space.buffer_time_minutes}, "
                         f"SpaceType: {target_space_type.default_buffer_time_minutes if target_space_type else 'N/A'})")

            try:
                date_utils.validate_booking_duration(start_time, end_time, effective_min_duration,
                                                     effective_max_duration)
                date_utils.validate_booking_daily_availability(start_time, end_time, effective_available_start_time,
                                                               effective_available_end_time)
            except Exception as e:
                error_detail = e.messages[0] if hasattr(e, 'messages') else str(e)
                logger.warning(f"Booking duration or daily availability validation failed: {error_detail}")
                raise BadRequestException(detail=error_detail, code="booking_duration_or_availability_invalid")

            # --- 用户禁用检查 ---
            user_ban_service = self._get_user_ban_service()
            is_banned_result = user_ban_service.is_user_banned(user, target_space_type)
            if not is_banned_result.success:
                logger.error(f"Failed to check user ban status for user {user.pk}: {is_banned_result.errors}")
                raise ServiceException(message="检查用户禁用状态失败。", errors=is_banned_result.errors,
                                       status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

            if is_banned_result.data:
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
                        status_code=http_status.HTTP_403_FORBIDDEN
                    )

            # --- 每日预订限制检查 (两种类型) ---
            today = start_time.date()
            daily_limit_service = self._get_daily_booking_limit_service()

            # 1. 检查针对当前空间类型的每日限制
            effective_space_type_limit = daily_limit_service.get_effective_daily_limit(user, target_space_type)

            if effective_space_type_limit > 0:
                current_bookings_count_for_space_type = self.booking_dao.get_user_bookings_count_for_date(
                    user=user,
                    target_date=today,
                    status_in=[Booking.BOOKING_STATUS_PENDING,
                               Booking.BOOKING_STATUS_APPROVED,
                               Booking.BOOKING_STATUS_CHECKED_IN],
                    space_type=target_space_type
                )

                if current_bookings_count_for_space_type + 1 > effective_space_type_limit:
                    raise ServiceException(
                        message=f"您在 {target_space_type.name if target_space_type else '全局'} 空间类型下，当日已达最大预订次数限制 ({effective_space_type_limit}次)。",
                        error_code="daily_booking_limit_exceeded",
                        status_code=http_status.HTTP_403_FORBIDDEN
                    )
                logger.info(f"User {user.pk} passed daily booking limit check for space type {target_space_type.name if target_space_type else 'None'} (limit: {effective_space_type_limit}, current: {current_bookings_count_for_space_type}).")

            # 2. 检查用户在所有空间类型下的每日总限制
            effective_total_daily_limit = daily_limit_service.get_effective_total_daily_limit(user)

            if effective_total_daily_limit > 0:
                current_total_bookings_count = self.booking_dao.get_user_total_bookings_count_for_date(
                    user=user,
                    target_date=today,
                    status_in=[Booking.BOOKING_STATUS_PENDING,
                               Booking.BOOKING_STATUS_APPROVED,
                               Booking.BOOKING_STATUS_CHECKED_IN]
                )

                if current_total_bookings_count + 1 > effective_total_daily_limit:
                    raise ServiceException(
                        message=f"您当日所有空间类型的预订总次数已达最大限制 ({effective_total_daily_limit}次)。",
                        error_code="total_daily_booking_limit_exceeded",
                        status_code=http_status.HTTP_403_FORBIDDEN
                    )
                logger.info(f"User {user.pk} passed total daily booking limit check (limit: {effective_total_daily_limit}, current: {current_total_bookings_count}).")

            # --- 每日预订限制检查 结束 ---

            # --- 资源冲突与容量检查 (核心逻辑) ---
            capacity_for_conflict_check: int
            if isinstance(target_obj, Space):
                capacity_for_conflict_check = 1
                logger.debug(f"Target {target_obj.name} (Space) has capacity_for_conflict_check: {capacity_for_conflict_check} (exclusive booking).")
            elif isinstance(target_obj, BookableAmenity):
                capacity_for_conflict_check = target_obj.quantity if target_obj.quantity is not None else 1
                logger.debug(f"Target {target_obj.amenity.name} (BookableAmenity) has capacity_for_conflict_check: {capacity_for_conflict_check}.")
            else:
                raise InternalServerError(detail="未知预订目标类型，无法进行容量检查。",
                                          code="unknown_target_type_for_capacity")

            # --- START OF MODIFICATION ---
            # 在调用 DAO 之前，根据缓冲时间调整查询的开始和结束时间
            query_start_time_with_buffer = start_time - timedelta(minutes=effective_buffer_time_minutes)
            query_end_time_with_buffer = end_time + timedelta(minutes=effective_buffer_time_minutes)

            # 获取所有与【带缓冲的新预订时间段】重叠的活跃预订
            overlapping_active_bookings_qs = self.booking_dao.get_overlapping_bookings(
                target_entity=target_obj,
                # 注意：这里传递的是已经包含了缓冲的查询时间窗口
                start_time=query_start_time_with_buffer, # <--- 修改点
                end_time=query_end_time_with_buffer,     # <--- 修改点
            )

            # 记录详细信息，以便诊断为何冲突检查可能失效
            logger.debug(
                f"Preliminary Time conflict check for {target_obj} (PK:{getattr(target_obj, 'pk', 'N/A')}) between "
                f"ORIGINAL {start_time.isoformat()} and {end_time.isoformat()}. "
                f"DAO query range (with buffer): {query_start_time_with_buffer.isoformat()} - {query_end_time_with_buffer.isoformat()}. "
                f"New booking quantity: {request_data.get('booked_quantity', 1)}. Total capacity: {capacity_for_conflict_check}."
            )
            booked_slots = [
                {'start_time': b.start_time, 'end_time': b.end_time, 'booked_quantity': b.booked_quantity}
                for b in overlapping_active_bookings_qs
            ]
            logger.debug(f"Found {len(booked_slots)} overlapping active bookings. Details: {booked_slots}")

            is_available = CommonBookingHelpers.is_time_slot_available(
                booked_slots=booked_slots,
                # 这里仍然传递原始的新预订时间，因为 common_helpers 内部会再次应用缓冲
                new_start=start_time, # <--- 保持原始时间
                new_end=end_time,     # <--- 保持原始时间
                booked_quantity=request_data.get('booked_quantity', 1),
                total_capacity=capacity_for_conflict_check,
                buffer_time_minutes=effective_buffer_time_minutes
            )
            # --- END OF MODIFICATION ---

            # 修改点：更具体地返回冲突原因
            if not is_available:
                detailed_conflict_messages = []
                for conf_booking in overlapping_active_bookings_qs:
                    entity_name = ""
                    entity_type = ""
                    if conf_booking.space and not conf_booking.bookable_amenity: # Direct space booking
                        entity_name = conf_booking.space.name
                        entity_type = "空间"
                    elif conf_booking.bookable_amenity and conf_booking.bookable_amenity.amenity: # Amenity booking
                        entity_name = conf_booking.bookable_amenity.amenity.name
                        entity_type = "设施"
                    elif conf_booking.related_space: # Fallback
                         entity_name = conf_booking.related_space.name
                         entity_type = "关联空间"

                    detailed_conflict_messages.append(
                        f"{entity_type} '{entity_name}' (ID: {conf_booking.pk}) "
                        f"在 [{conf_booking.start_time.strftime('%H:%M')}-{conf_booking.end_time.strftime('%H:%M')}]"
                    )

                if detailed_conflict_messages:
                    conflict_reason = "预订失败。与以下预订时间冲突或容量不足：\n" + "\n".join(
                        [f"{i+1}. {msg}" for i, msg in enumerate(detailed_conflict_messages)]
                    )
                else:
                    # 这应该是一个不常发生的 fallback，因为如果 is_available 返回 False，
                    # 那么 overlapping_active_bookings_qs 应该有内容。
                    conflict_reason = "预订时间段与现有预订冲突，但未能识别具体冲突细节或资源容量不足。"

                logger.warning(f"Booking for {target_obj} failed time/capacity conflict check at preliminary stage. "
                               f"Conflict reason: {conflict_reason}")
                raise ConflictException(detail=conflict_reason,
                                        code="booking_time_capacity_conflict")
            logger.info(f"Booking for {target_obj} passed time/capacity conflict check at preliminary stage.")
            # --- 资源冲突与容量检查 结束 ---

            # --- 权限检查 ---
            if not (user.is_superuser or user.is_system_admin):
                if target_space_type and target_space_type.is_basic_infrastructure:
                    pass
                elif not target_space.permitted_groups.filter(pk__in=user.groups.all()).exists():
                    raise ForbiddenException(detail="您没有权限预订此空间/设施。",
                                             code="user_unauthorized_to_book")
            logger.info(f"Booking for {target_obj} passed user group permission check.")

            # --- 预期参与人数检查 ---
            if isinstance(target_obj, Space) and request_data.get('expected_attendees') is not None:
                expected_attendees = request_data['expected_attendees']
                if expected_attendees <= 0:
                    raise BadRequestException(detail="预期参与人数必须大于0。", code="invalid_expected_attendees")
                if target_obj.capacity is not None and expected_attendees > target_obj.capacity:
                    raise BadRequestException(
                        detail=f"预期参与人数 {expected_attendees} 超过空间最大物理容量 {target_obj.capacity}。",
                        code="exceeds_space_physical_capacity")
            logger.info(f"Booking for {target_obj} passed expected attendees check.")

            # --- 创建初步预订实例并调度异步任务 ---
            required_booking_fields = {
                'user': user,
                'request_uuid': request_uuid,
                'start_time': start_time,
                'end_time': end_time,
                'booked_quantity': request_data.get('booked_quantity', 1),
                'purpose': request_data.get('purpose', ''),
            }

            if target_obj:
                if isinstance(target_obj, Space):
                    required_booking_fields['space'] = target_obj
                    # For spaces, capacity is 1 booking for the entire space, so booked_quantity for space should be 1
                    required_booking_fields['booked_quantity'] = 1 # 强制预订空间时数量为1
                elif isinstance(target_obj, BookableAmenity):
                    required_booking_fields['bookable_amenity'] = target_obj

            if 'expected_attendees' in request_data and request_data['expected_attendees'] is not None:
                required_booking_fields['expected_attendees'] = request_data['expected_attendees']

            with transaction.atomic():
                initial_booking_instance = self.booking_dao.create_booking(
                    status=Booking.BOOKING_STATUS_PENDING,
                    processing_status=Booking.PROCESSING_STATUS_SUBMITTED,
                    **required_booking_fields
                )

            booking_tasks.process_booking_creation_task.delay(initial_booking_instance.pk)

            logger.info(
                f"Preliminary validation successful for request_uuid {request_uuid}. Booking ID {initial_booking_instance.pk} created in SUBMITTED state, deep validation task dispatched.")

            return ServiceResult.success_result(
                data={
                    'booking_id': initial_booking_instance.pk,
                    'request_uuid': str(initial_booking_instance.request_uuid),
                },
                message="预订请求已提交，正在处理中。",
                status_code=http_status.HTTP_202_ACCEPTED
            )

        except ServiceException as e:
            logger.warning(f"Preliminary validation failed (ServiceException): {e.message} (Code: {e.error_code})")
            if e.error_code in ["booking_time_capacity_conflict", "daily_booking_limit_exceeded", "total_daily_booking_limit_exceeded", "request_uuid_failed_previous_validation", "user_banned"]:
                e.status_code = http_status.HTTP_409_CONFLICT
            raise e
        except CustomAPIException as e:
            logger.warning(f"Preliminary validation failed (CustomAPIException): {e.detail}")
            if isinstance(e, ConflictException):
                e.status_code = http_status.HTTP_409_CONFLICT
            raise e
        except Exception as e:
            logger.exception(
                f"Unhandled error during preliminary booking validation for user {user.pk}, request_uuid {request_data.get('request_uuid')}.")
            raise InternalServerError(detail=f"初步预订验证失败，发生未知错误: {str(e)}", code="preliminary_validation_runtime_error")