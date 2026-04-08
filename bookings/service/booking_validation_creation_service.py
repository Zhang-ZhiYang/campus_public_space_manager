import logging
from typing import Dict, Any, Tuple, Optional, Union
from datetime import datetime, timedelta, date # 导入 timedelta

from django.db import transaction
from django.db.models import F, Sum, Q
from django.utils import timezone
from rest_framework import status as http_status

from bookings.service import DailyBookingLimitService, UserBanService, UserExemptionService
from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.service.factory import ServiceFactory
from core.service.cache import CacheService
from core.utils.exceptions import ServiceException, NotFoundException, BadRequestException, ForbiddenException, \
    ConflictException, InternalServerError
from core.utils import date_utils
from bookings.service.common_helpers import CommonBookingHelpers

from users.models import CustomUser
from spaces.models import Space, BookableAmenity, SpaceType
from bookings.models import Booking
from django.contrib.auth.models import Group

logger = logging.getLogger(__name__)

class BookingValidationCreationService(BaseService):
    _dao_map = {
        'booking_dao': 'booking',
        'space_dao': 'space',
        'bookable_amenity_dao': 'bookable_amenity',
        'user_penalty_points_dao': 'user_penalty_points',
    }

    def __init__(self):
        super().__init__()
        self.booking_dao = self._get_dao_instance('booking')
        self.space_dao = self._get_dao_instance('space')
        self.bookable_amenity_dao = self._get_dao_instance('bookable_amenity')
        self.user_penalty_points_dao = self._get_dao_instance('user_penalty_points')

        self._daily_booking_limit_service: Optional['DailyBookingLimitService'] = None
        self._user_ban_service: Optional['UserBanService'] = None
        self._user_exemption_service: Optional['UserExemptionService'] = None

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

    def deep_validate_and_confirm(self, booking_id: int) -> ServiceResult[Booking]:
        booking_instance: Optional[Booking] = None
        current_status_message = ""

        try:
            with transaction.atomic():
                booking_instance = self.booking_dao.get_queryset().select_for_update().filter(pk=booking_id).first()

                if not booking_instance:
                    logger.error(f"Deep validation: Booking ID {booking_id} not found for deep validation.")
                    raise NotFoundException(detail="预订记录未找到。")

                if booking_instance.processing_status not in [
                    Booking.PROCESSING_STATUS_SUBMITTED,
                    Booking.PROCESSING_STATUS_IN_PROGRESS
                ]:
                    logger.info(
                        f"Deep validation: Booking ID {booking_id} is in status {booking_instance.processing_status}, skipping deep validation as it's already processed or failed.")
                    return ServiceResult.success_result(
                        data=booking_instance,
                        message=f"预订 {booking_id} 已经处理过，当前状态为 {booking_instance.get_processing_status_display()}"
                    )

                current_status_message = "预订请求正在进行深层校验。"
                self.booking_dao.update_booking_processing_status(
                    booking_id,
                    Booking.PROCESSING_STATUS_IN_PROGRESS,
                    admin_notes=current_status_message
                )
                logger.info(f"Booking ID {booking_id} updated to IN_PROGRESS.")

                target_obj: Union[Space, BookableAmenity]
                target_space: Space

                if booking_instance.space:
                    target_obj = self.space_dao.get_queryset().select_for_update().get(pk=booking_instance.space_id)
                    target_space = target_obj
                elif booking_instance.bookable_amenity:
                    target_obj = self.bookable_amenity_dao.get_queryset().select_for_update().get(
                        pk=booking_instance.bookable_amenity_id)
                    target_space = target_obj.space
                else:
                    raise InternalServerError(detail="预订记录无有效目标，数据异常。", code="invalid_booking_target")

                if not target_space:
                    raise InternalServerError(detail="无法确定预订目标所属的空间，系统内部错误。",
                                              code='missing_related_space_internal')

                target_space_type: Optional[SpaceType] = target_space.space_type

                user_penalty_points_record = self.user_penalty_points_dao.get_queryset().select_for_update().filter(
                    user=booking_instance.user,
                    space_type=target_space_type
                ).first()

                effective_booking_capacity: int

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
                    if booking_instance.booked_quantity != 1:
                        raise BadRequestException(detail="预订整个空间时，数量必须为1。",
                                                  code="invalid_space_booking_quantity_locked")

                    effective_booking_capacity = target_obj.capacity if target_obj.capacity is not None else 1
                    logger.debug(
                        f"Target {target_obj.name} (Space) has effective_booking_capacity: {effective_booking_capacity}")
                    target_physical_capacity = target_obj.capacity

                elif isinstance(target_obj, BookableAmenity):
                    if not target_obj.is_bookable:
                        raise BadRequestException(
                            detail=f"设施 '{target_obj.amenity.name}' (ID: {target_obj.pk}) 当前不可预订。",
                            code="amenity_not_bookable_locked")
                    if not target_obj.is_active:
                        raise BadRequestException(
                            detail=f"设施 '{target_obj.amenity.name}' (ID: {target_obj.pk}) 当前不活跃。",
                            code="amenity_not_active_locked")

                    effective_booking_capacity = target_obj.quantity if target_obj.quantity is not None else 1
                    logger.debug(
                        f"Target {target_obj.amenity.name} (BookableAmenity) has effective_booking_capacity: {effective_booking_capacity}.")

                    if booking_instance.booked_quantity <= 0:
                        raise BadRequestException(detail="预订数量必须大于0。", code="invalid_booking_quantity_locked")
                    if effective_booking_capacity is not None and booking_instance.booked_quantity > effective_booking_capacity:
                        raise BadRequestException(
                            detail=f"预订数量 {booking_instance.booked_quantity} 超过设施总数量 {effective_booking_capacity}。",
                            code="exceeds_amenity_capacity_locked")
                    target_physical_capacity = target_space.capacity

                else:
                    raise InternalServerError(detail="未知预订目标类型。", code="unknown_target_type")

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
                    logger.warning(f"Booking duration or daily availability validation failed (locked): {error_detail}")
                    raise BadRequestException(detail=error_detail, code="invalid_booking_time_locked")

                if not (booking_instance.user.is_superuser or booking_instance.user.is_system_admin):
                    if target_space_type and target_space_type.is_basic_infrastructure:
                        pass
                    elif not hasattr(booking_instance.user, 'groups') or \
                            not target_space.permitted_groups.filter(
                                pk__in=booking_instance.user.groups.all()).exists():
                        raise ForbiddenException(detail="您没有权限预订此空间/设施。",
                                                 code="user_unauthorized_to_book_locked")
                logger.info(f"Booking {booking_id} passed user group permission check (locked).")

                if isinstance(target_obj, Space) and booking_instance.expected_attendees is not None:
                    if booking_instance.expected_attendees <= 0:
                        raise BadRequestException(detail="预期参与人数必须大于0。",
                                                  code="invalid_expected_attendees_locked")
                    if target_physical_capacity is not None and booking_instance.expected_attendees > target_physical_capacity:
                        raise BadRequestException(
                            detail=f"预期参与人数 {booking_instance.expected_attendees} 超过空间最大物理容量 {target_physical_capacity}。",
                            code="exceeds_space_physical_capacity_locked")
                logger.info(f"Booking {booking_id} passed expected attendees check (locked).")

                # 在调用 DAO 之前，根据缓冲时间调整查询的开始和结束时间
                query_start_time_with_buffer = booking_instance.start_time - timedelta(minutes=effective_buffer_time_minutes)
                query_end_time_with_buffer = booking_instance.end_time + timedelta(minutes=effective_buffer_time_minutes)

                overlapping_bookings_qs = self.booking_dao.get_overlapping_bookings(
                    target_entity=target_obj,
                    start_time=query_start_time_with_buffer,
                    end_time=query_end_time_with_buffer,
                    exclude_booking_id=booking_instance.pk if booking_instance.pk else None
                ).select_for_update()

                logger.debug(f"Resource Conflict Check for Booking {booking_id} (deep validation): "
                             f"Target: {target_obj} (PK:{getattr(target_obj, 'pk', 'N/A')}), "
                             f"Booking Quantity: {booking_instance.booked_quantity}, "
                             f"Effective Capacity: {effective_booking_capacity}. "
                             f"DAO query range (with buffer): {query_start_time_with_buffer.isoformat()} - {query_end_time_with_buffer.isoformat()}. "
                             f"Found {overlapping_bookings_qs.count()} overlapping (PENDING/APPROVED/CHECKED_IN) bookings.")

                booked_slots = [
                    {'start_time': b.start_time, 'end_time': b.end_time, 'booked_quantity': b.booked_quantity}
                    for b in overlapping_bookings_qs
                ]
                logger.debug(f"Overlapping booked slots details: {booked_slots}")

                is_available = CommonBookingHelpers.is_time_slot_available(
                    booked_slots=booked_slots,
                    new_start=booking_instance.start_time,
                    new_end=booking_instance.end_time,
                    booked_quantity=booking_instance.booked_quantity,
                    total_capacity=effective_booking_capacity,
                    buffer_time_minutes=effective_buffer_time_minutes
                )

                if not is_available:
                    detailed_conflict_messages = []
                    for conf_booking in overlapping_bookings_qs:
                        entity_name = ""
                        entity_type = ""
                        if conf_booking.space and not conf_booking.bookable_amenity:
                            entity_name = conf_booking.space.name
                            entity_type = "空间"
                        elif conf_booking.bookable_amenity and conf_booking.bookable_amenity.amenity:
                            entity_name = conf_booking.bookable_amenity.amenity.name
                            entity_type = "设施"
                        elif conf_booking.related_space:
                            entity_name = conf_booking.related_space.name
                            entity_type = "关联空间"

                        detailed_conflict_messages.append(
                            f"{entity_type} '{entity_name}' (ID: {conf_booking.pk}) "
                            f"在 [{conf_booking.start_time.strftime('%H:%M')}-{conf_booking.end_time.strftime('%H:%M')}]"
                        )

                    if detailed_conflict_messages:
                        conflict_reason = "预订失败。与以下预订时间冲突或容量不足：\n" + "\n".join(
                            [f"{i + 1}. {msg}" for i, msg in enumerate(detailed_conflict_messages)]
                        )
                    else:
                        conflict_reason = "预订时间段与现有预订冲突，但未能识别具体冲突细节或资源容量不足。"

                    logger.warning(f"Booking {booking_id} failed resource conflict check (deep validation). "
                                   f"Conflict reason: {conflict_reason}")
                    raise ConflictException(detail=conflict_reason,
                                            code="booking_time_conflict_locked")
                logger.info(f"Booking {booking_id} passed resource conflict check (deep validation).")

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
                logger.info(f"Booking {booking_id} passed user ban/exemption check (locked).")

                daily_limit_service = self._get_daily_booking_limit_service()
                effective_limit = daily_limit_service.get_effective_daily_limit(booking_instance.user,
                                                                                target_space_type)

                if effective_limit > 0:
                    today = booking_instance.start_time.date()
                    current_bookings_count = self.booking_dao.get_user_bookings_count_for_date(
                        user=booking_instance.user,
                        target_date=today,
                        status_in=[Booking.BOOKING_STATUS_PENDING,
                                   Booking.BOOKING_STATUS_APPROVED,
                                   Booking.BOOKING_STATUS_CHECKED_IN],
                        space_type=target_space_type,
                        exclude_booking_id=booking_instance.pk
                    )

                    if current_bookings_count + 1 > effective_limit:
                        logger.warning(
                            f"Booking {booking_id} failed daily limit check for user {booking_instance.user.pk} (locked). "
                            f"Current count {current_bookings_count}, Limit {effective_limit}.")
                        raise ForbiddenException(
                            message=f"您在 {target_space_type.name if target_space_type else '全局'} 空间类型下，当日已达最大预订次数限制 ({effective_limit}次)。",
                            status_code=http_status.HTTP_403_FORBIDDEN
                        )
                logger.info(f"Booking {booking_id} passed daily booking limit check (locked).")

                effective_total_daily_limit = daily_limit_service.get_effective_total_daily_limit(booking_instance.user)
                if effective_total_daily_limit > 0:
                    current_total_bookings_count = self.booking_dao.get_user_total_bookings_count_for_date(
                        user=booking_instance.user,
                        target_date=today,
                        status_in=[Booking.BOOKING_STATUS_PENDING,
                                   Booking.BOOKING_STATUS_APPROVED,
                                   Booking.BOOKING_STATUS_CHECKED_IN],
                        exclude_booking_id=booking_instance.pk
                    )

                    if current_total_bookings_count + 1 > effective_total_daily_limit:
                        logger.warning(
                            f"Booking {booking_id} failed total daily limit check for user {booking_instance.user.pk} (locked). "
                            f"Current total effective count {current_total_bookings_count}, Limit {effective_total_daily_limit}.")
                        raise ForbiddenException(
                            message=f"您当日所有空间类型的预订总次数已达最大限制 ({effective_total_daily_limit}次)。",
                            error_code="total_daily_booking_limit_exceeded_locked",
                            status_code=http_status.HTTP_403_FORBIDDEN
                        )
                logger.info(f"Booking {booking_id} passed total daily booking limit check (locked).")

                # --- 【核心逻辑优化开始】 ---
                booking_instance.processing_status = Booking.PROCESSING_STATUS_CREATED

                admin_notes = "深层校验通过，预订已确认。"
                if booking_instance.status == Booking.BOOKING_STATUS_PENDING:
                    admin_notes += " 等待管理员审批。"

                booking_instance.admin_notes = admin_notes
                booking_instance.save() # 直接调用 .save() 确保触发模型层的所有逻辑

                logger.info(
                    f"Deep validation successful and booking ID {booking_id} confirmed in its initial '{booking_instance.status}' status.")

                return ServiceResult.success_result(
                    data=booking_instance,
                    message="预订已成功确认。",
                    status_code=http_status.HTTP_201_CREATED
                )
                # --- 【核心逻辑优化结束】 ---

        except ServiceException as e:
            current_status_message = f"深层校验失败: {e.message} (Code: {e.error_code})"
            logger.warning(f"Deep validation failed (ServiceException) for booking ID {booking_id}: {current_status_message}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_id,
                    Booking.PROCESSING_STATUS_FAILED_VALIDATION,
                    admin_notes=current_status_message
                )
            raise e

        except NotFoundException as e:
            current_status_message = f"深层校验失败: {e.detail}"
            logger.warning(f"Deep validation failed (NotFoundException) for booking ID {booking_id}: {current_status_message}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_id,
                    Booking.PROCESSING_STATUS_FAILED_VALIDATION,
                    admin_notes=current_status_message
                )
            raise e

        except BadRequestException as e:
            current_status_message = f"深层校验失败: {e.detail}"
            logger.warning(f"Deep validation failed (BadRequestException) for booking ID {booking_id}: {current_status_message}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_id,
                    Booking.PROCESSING_STATUS_FAILED_VALIDATION,
                    admin_notes=current_status_message
                )
            raise e

        except ForbiddenException as e:
            current_status_message = f"深层校验失败: {e.detail}"
            logger.warning(f"Deep validation failed (ForbiddenException) for booking ID {booking_id}: {current_status_message}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_id,
                    Booking.PROCESSING_STATUS_FAILED_VALIDATION,
                    admin_notes=current_status_message
                )
            raise e

        except ConflictException as e:
            current_status_message = f"深层校验失败: {e.detail}"
            logger.warning(f"Deep validation failed (ConflictException) for booking ID {booking_id}: {current_status_message}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_id,
                    Booking.PROCESSING_STATUS_FAILED_VALIDATION,
                    admin_notes=current_status_message
                )
            raise e

        except Exception as e:
            current_status_message = f"深层校验运行时错误: {str(e)}"
            logger.exception(f"Unhandled error during deep booking validation for booking ID {booking_id}: {e}")
            if booking_instance:
                self.booking_dao.update_booking_processing_status(
                    booking_id,
                    Booking.PROCESSING_STATUS_FAILED_RUNTIME,
                    admin_notes=current_status_message
                )
            raise InternalServerError(detail=current_status_message, code="deep_validation_runtime_error")