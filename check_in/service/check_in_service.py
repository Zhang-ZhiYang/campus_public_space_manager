import logging
from typing import Dict, Any, Optional, List, Union
from django.db import transaction
from django.utils import timezone
from datetime import datetime, timedelta
from math import radians, sin, cos, sqrt, atan2

from core.service import BaseService, ServiceResult
from core.dao import DAOFactory
from core.utils.exceptions import NotFoundException, BadRequestException, ForbiddenException, ConflictException, \
    InternalServerError, CustomAPIException
from check_in.models import CheckInRecord # 确保导入 CheckInRecord
from bookings.models import Booking
from spaces.models import Space, CHECK_IN_METHOD_NONE, CHECK_IN_METHOD_SELF, CHECK_IN_METHOD_STAFF, \
    CHECK_IN_METHOD_HYBRID, CHECK_IN_METHOD_LOCATION
from users.models import CustomUser

from core.service.cache import CacheService

logger = logging.getLogger(__name__)

# 配置：定位签到的有效半径（公里） - 此配置在此简化后将不再被后端使用，但保留以供参考
LOCATION_CHECK_IN_RADIUS_KM = 0.3  # 50米
# 配置：允许签到的提前或滞后时间窗口（分钟） - 此配置在此简化后将不再被后端使用
CHECK_IN_WINDOW_MINUTES = 15
# 配置：签到后预订状态更新的缓冲时间。例如预订结束后多久还可以处理签到 - 此配置在此简化后将不再被后端使用
CHECK_IN_GRACE_PERIOD_MINUTES = 0

class CheckInService(BaseService):
    _dao_map = {
        'check_in_record_dao': 'check_in_record',
        'booking_dao': 'booking',
        'space_dao': 'space',
    }
    _allowed_prefetch_related = ['booking__user', 'booking__related_space', 'checked_in_by']
    _allowed_select_related = ['booking__user', 'booking__related_space__space_type', 'checked_in_by']

    def __init__(self):
        super().__init__()
        self._check_in_record_dao = DAOFactory.get_dao('check_in_record')
        self._booking_dao = DAOFactory.get_dao('booking')
        self._space_dao = DAOFactory.get_dao('space')

    def _get_booking_and_space_for_check_in(self, booking_pk: int) -> ServiceResult[Dict[str, Any]]:
        """
        根据 booking_pk 获取预订及其关联空间。
        简化：不再检查预订状态是否为 APPROVED，仅确保预订和空间存在且活跃可预订。
        """
        try:
            try:
                booking = self._booking_dao._manager.select_related(
                    'user',
                    'space',
                    'space__space_type',
                    'bookable_amenity',
                    'bookable_amenity__space',
                    'bookable_amenity__space__space_type',
                    'related_space',
                    'related_space__space_type'
                ).prefetch_related('check_in_records').get(pk=booking_pk) # <--- 添加 prefetch_related
            except Booking.DoesNotExist:
                return ServiceResult.error_result(
                    message="预订未找到。",
                    error_code="booking_not_found",
                    status_code=404
                )

            target_space = booking.related_space if booking.space else (
                booking.bookable_amenity.space if booking.bookable_amenity else None
            )

            if not target_space:
                return ServiceResult.error_result(
                    message="预订未关联到有效的空间，无法签到。",
                    error_code="space_not_found_for_booking",
                    status_code=400
                )

            # 确保空间是活动的且可预订 (这些基础检查仍应保留)
            if not target_space.is_active:
                return ServiceResult.error_result(
                    message=f"空间 {target_space.name} 不活跃，无法签到。",
                    error_code="space_inactive",
                    status_code=400
                )
            if not target_space.is_bookable:
                return ServiceResult.error_result(
                    message=f"空间 {target_space.name} 不可预订，无法签到。",
                    error_code="space_not_bookable",
                    status_code=400
                )

            return ServiceResult.success_result(data={'booking': booking, 'space': target_space})
        except Exception as e:
            logger.exception(f"获取预订和空间信息失败 (booking_pk: {booking_pk})。")
            return self._handle_exception(e, default_message="获取预订和空间信息失败。")

    # 移除 _check_check_in_time_window 方法
    # 移除 _haversine_distance 方法
    # 移除 _validate_location_check_in 方法

    @transaction.atomic
    def perform_check_in(self, user: CustomUser, booking_pk: int,
                         latitude: Optional[float] = None,
                         longitude: Optional[float] = None,
                         photo: Optional[Any] = None,  # File object
                         notes: Optional[str] = None,
                         client_check_in_method: str = CHECK_IN_METHOD_HYBRID  # NEW: 前端告知的签到方式
                         ) -> ServiceResult[Dict[str, Any]]:
        """
        执行单个预订的签到操作。
        简化：只验证预订状态是否为 'APPROVED'，并根据前端告知的签到方式强制检查必要数据。
        不再进行时间窗口、距离、重复签到和复杂的权限判断。
        """
        try:
            # 1. 获取预订和空间信息
            get_booking_space_result = self._get_booking_and_space_for_check_in(booking_pk)
            if not get_booking_space_result.success:
                return get_booking_space_result

            booking = get_booking_space_result.data['booking']
            space = get_booking_space_result.data['space']

            # 2. 核心验证：预订状态必须是 'APPROVED'
            if booking.status != Booking.BOOKING_STATUS_APPROVED:
                return ServiceResult.error_result(
                    message=f"该预订当前状态为 '{booking.get_status_display()}'，只有已批准的预订才能签到。",
                    error_code="booking_not_approved_for_check_in",
                    status_code=400
                )

            # 简化：不再进行重复签到验证
            # if self._check_in_record_dao.get_record_by_booking_id(booking_pk):
            #     return ServiceResult.error_result(
            #         message="该预订已签到，请勿重复操作。",
            #         error_code="already_checked_in",
            #         status_code=409
            #     )

            # 3. 根据前端告知的签到方式，强制检查必要数据
            checked_in_by_user = user  # 签到执行人默认为当前操作用户

            if client_check_in_method == CHECK_IN_METHOD_NONE:
                return ServiceResult.error_result(
                    message=f"前端请求的签到方式为 '不需要签到'，此接口不处理。",
                    error_code="check_in_not_required_by_client",
                    status_code=400
                )
            elif client_check_in_method == CHECK_IN_METHOD_LOCATION:
                if latitude is None or longitude is None:
                    return ServiceResult.error_result(
                        message="前端告知为定位签到，但未提供地理坐标。",
                        error_code="location_coordinates_missing",
                        status_code=400
                    )
            elif client_check_in_method in [CHECK_IN_METHOD_SELF, CHECK_IN_METHOD_HYBRID]:
                # 假设 SELF 和 HYBRID 模式下强制要求照片
                if not photo:
                    return ServiceResult.error_result(
                        message=f"前端告知签到方式为 '{client_check_in_method}'，需要提供照片凭证。",
                        error_code="photo_required_for_check_in_method",
                        status_code=400
                    )
            # 如果是 CHECK_IN_METHOD_STAFF，则无需额外强制数据

            # 简化：移除所有时间窗口验证
            # 简化：移除所有定位距离验证
            # 简化：移除所有复杂权限验证

            # 4. 创建签到记录 (check_in_method 记录前端告知的实际签到方式)
            check_in_record = self._check_in_record_dao.create(
                booking=booking,
                user=booking.user,  # 签到记录的主体用户始终是预订人
                checked_in_by=checked_in_by_user,  # 执行签到操作的用户
                check_in_time=timezone.now(),
                check_in_method=client_check_in_method,  # NEW: 使用前端告知的签到方式
                latitude=latitude,
                longitude=longitude,
                check_in_image=photo,
                notes=notes
            )

            # 5. 更新预订状态为已签到
            updated_booking = self._booking_dao.update(
                booking,
                status=Booking.BOOKING_STATUS_CHECKED_IN,
                actual_check_in_time=timezone.now()
            )

            if not updated_booking:
                raise InternalServerError("签到后更新预订状态失败。")

            CacheService.invalidate_object_cache('bookings:booking', booking_pk)
            logger.info(
                f"CheckInService: Successfully invalidated cache for bookings:booking:{booking_pk} after check-in.")

            latest_booking = self._booking_dao.get_booking_by_id(booking_pk)
            if not latest_booking:
                logger.error(
                    f"CheckInService: Failed to retrieve latest booking {booking_pk} after successful check-in and cache invalidation.")
                return ServiceResult.error_result(message="签到成功但获取最新预订信息失败。", status_code=500)

            # 6. 返回更新后的预订数据 (包含 CheckInRecord)
            # latest_booking.to_dict() 现在应该会包含嵌套的 check_in_record
            return ServiceResult.success_result(
                data=latest_booking.to_dict(include_related=True), # 确保包含关联数据
                message="签到成功。",
                status_code=201
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"执行签到失败 (User: {user.username}, Booking PK: {booking_pk}).")
            return self._handle_exception(e, default_message="签到失败，发生未知错误。")

    def get_check_in_record_by_booking(self, user: CustomUser, booking_pk: int) -> ServiceResult[Dict[str, Any]]:
        """
        获取某个预订的签到详情。
        此方法权限验证保持不变。
        """
        try:
            try:
                booking = self._booking_dao._manager.select_related(
                    'user',
                    'space',
                    'space__space_type',
                    'bookable_amenity',
                    'bookable_amenity__space',
                    'bookable_amenity__space__space_type',
                    'related_space',
                    'related_space__space_type'
                ).get(pk=booking_pk)
            except Booking.DoesNotExist:
                return ServiceResult.error_result(
                    message="预订未找到。",
                    error_code="booking_not_found",
                    status_code=404
                )

            # 权限：只有预订人或工作人员可以查看其签到记录
            can_view = user == booking.user or \
                       user.is_system_admin or \
                       user.is_space_manager or \
                       (user.is_check_in_staff and user.has_perm('spaces.can_check_in_real_space',
                                                                 booking.related_space))

            if not can_view:
                return ServiceResult.error_result(
                    message="您没有权限查看此预订的签到记录。",
                    error_code="not_authorized_to_view_check_in",
                    status_code=403
                )

            record = self._check_in_record_dao.get_record_by_booking_id(booking_pk)
            if not record:
                return ServiceResult.error_result(
                    message="该预订尚未签到。",
                    error_code="check_in_record_not_found",
                    status_code=404
                )

            return ServiceResult.success_result(
                data=record.to_dict(),
                message="成功获取签到记录详情。",
                status_code=200
            )
        except Exception as e:
            logger.exception(f"获取签到记录失败 (Booking PK: {booking_pk}, User: {user.username}).")
            return self._handle_exception(e, default_message="获取签到记录失败。")