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
from check_in.models import CheckInRecord
from bookings.models import Booking
from spaces.models import Space, CHECK_IN_METHOD_NONE, CHECK_IN_METHOD_SELF, CHECK_IN_METHOD_STAFF, \
    CHECK_IN_METHOD_HYBRID, CHECK_IN_METHOD_LOCATION
from users.models import CustomUser

# NEW: 导入 CacheService
from core.service.cache import CacheService

logger = logging.getLogger(__name__)

# 配置：定位签到的有效半径（公里）
LOCATION_CHECK_IN_RADIUS_KM = 0.2  # 50米
# 配置：允许签到的提前或滞后时间窗口（分钟）
# 例如，如果预订 10:00 开始，窗口 15 分钟，则 09:45 可签到
CHECK_IN_WINDOW_MINUTES = 15
# 配置：签到后预订状态更新的缓冲时间。例如预订结束后多久还可以处理签到
CHECK_IN_GRACE_PERIOD_MINUTES = 0

class CheckInService(BaseService):
    _dao_map = {
        'check_in_record_dao': 'check_in_record',
        'booking_dao': 'booking',  # 需要访问 booking DAO
        'space_dao': 'space',  # 需要访问 space DAO
    }
    # 这些预加载提示仅供 Service 内部参考，实际查询需要在 DAO 或直接 ORM 层实现
    _allowed_prefetch_related = ['booking__user', 'booking__related_space', 'checked_in_by']
    _allowed_select_related = ['booking__user', 'booking__related_space__space_type', 'checked_in_by']

    def __init__(self):
        super().__init__()
        # 获取 DAO 实例
        self._check_in_record_dao = DAOFactory.get_dao('check_in_record')
        self._booking_dao = DAOFactory.get_dao('booking')
        self._space_dao = DAOFactory.get_dao('space')

    def _get_booking_and_space_for_check_in(self, booking_pk: int) -> ServiceResult[Dict[str, Any]]:
        """
        根据 booking_pk 获取预订及其关联空间，用于签到前的验证。
        封装 ServiceResult 返回，避免重复 try-except。
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

            # 修正：将 Booking.BOOKING_STATUS_CONFIRMED 改为 Booking.BOOKING_STATUS_APPROVED
            # if booking.status != Booking.BOOKING_STATUS_APPROVED:
            #     return ServiceResult.error_result(
            #         message="该预订尚未批准或已完成/取消，无法签到。",  # 消息也相应更新
            #         error_code="booking_not_approved",  # 错误码也建议更新，更精确表达
            #         status_code=400
            #     )

            # 获取关联的空间
            target_space = booking.related_space if booking.space else (
                booking.bookable_amenity.space if booking.bookable_amenity else None
            )

            if not target_space:
                return ServiceResult.error_result(
                    message="预订未关联到有效的空间，无法签到。",
                    error_code="space_not_found_for_booking",
                    status_code=400
                )

            # 确保空间是活动的且可预订
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

    def _check_check_in_time_window(self, booking: Booking) -> ServiceResult[None]:
        """
        校验签到时间是否在预订开始前 N 分钟到预订结束前 N 分钟之间。
        """
        now = timezone.now()
        check_in_start_window = booking.start_time - timedelta(minutes=CHECK_IN_WINDOW_MINUTES)
        # 签到结束窗口：可以是预订结束时间 + 缓冲时间，允许迟到签到
        check_in_end_window = booking.end_time + timedelta(minutes=CHECK_IN_GRACE_PERIOD_MINUTES)

        if not (check_in_start_window <= now <= check_in_end_window):
            return ServiceResult.error_result(
                message=f"不在签到时间窗口内。请在预订开始前 {CHECK_IN_WINDOW_MINUTES} 分钟 ({check_in_start_window.strftime('%Y-%m-%d %H:%M')}) 到预订结束后 {CHECK_IN_GRACE_PERIOD_MINUTES} 分钟 ({check_in_end_window.strftime('%Y-%m-%d %H:%M')}) 之间签到。",
                error_code="outside_check_in_window",
                status_code=400
            )
        return ServiceResult.success_result(None)

    def _haversine_distance(self, lat1, lon1, lat2, lon2):
        """
        计算两个经纬度坐标之间的哈弗面距离（公里）。
        参考：https://en.wikipedia.org/wiki/Haversine_formula
        """
        R = 6371  # 地球平均半径，单位公里

        lat1_rad = radians(lat1)
        lon1_rad = radians(lon1)
        lat2_rad = radians(lat2)
        lon2_rad = radians(lon2)

        dlon = lon2_rad - lon1_rad
        dlat = lat2_rad - lat1_rad

        a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    def _validate_location_check_in(self, space: Space, latitude: Optional[float], longitude: Optional[float]) -> \
            ServiceResult[None]:
        """
        校验定位签到是否符合要求，即经纬度是否在空间有效范围内。
        """
        if space.latitude is None or space.longitude is None:
            return ServiceResult.error_result(
                message=f"空间 {space.name} 未设置地理坐标，无法进行定位签到。",
                error_code="space_location_not_set",
                status_code=400
            )

        if latitude is None or longitude is None:
            return ServiceResult.error_result(
                message="执行定位签到必须提供地理坐标。",
                error_code="location_coordinates_missing",
                status_code=400
            )

        distance = self._haversine_distance(latitude, longitude, float(space.latitude), float(space.longitude))
        if distance > LOCATION_CHECK_IN_RADIUS_KM:
            return ServiceResult.error_result(
                message=f"您当前位置距离空间 {space.name} 过远 ({distance * 1000:.2f}米)，请靠近后重试。",
                error_code="location_too_far",
                status_code=400
            )

        return ServiceResult.success_result(None)

    @transaction.atomic
    def perform_check_in(self, user: CustomUser, booking_pk: int,
                         # client_request_method: str,  # NEW: 客户端不再直接发送 client_request_method, 而是通过 endpoint 区分
                         latitude: Optional[float] = None,
                         longitude: Optional[float] = None,
                         photo: Optional[Any] = None,  # File object
                         notes: Optional[str] = None,
                         is_staff_manual_check_in: bool = False # NEW: 增加一个明确的参数，指示是否为工作人员手动/代签，将用于 Service 内部的权限/逻辑判断
                         ) -> ServiceResult[Dict[str, Any]]:
        """
        执行单个预订的签到操作。
        适配拍照、扫码、定位这三种签到方式。

        :param user: 当前请求的用户。
        :param booking_pk: 预订的 ID。
        # :param client_request_method: 客户端尝试进行的签到方式 (例如 'PHOTO', 'QR', 'LOCATION', 'SELF_MANUAL')。
        #                               此参数用于记录实际签到方式及在 Service 内部进行辅助判断。
        :param latitude: 签到时的纬度。
        :param longitude: 签到时的经度。
        :param photo: 签到时上传的图片文件。
        :param notes: 签到备注。
        :param is_staff_manual_check_in: 布尔值，如果为 True，表示工作人员正在对非本人的预订进行签到。
        """
        try:
            # 1. 获取预订和空间信息
            get_booking_space_result = self._get_booking_and_space_for_check_in(booking_pk)
            if not get_booking_space_result.success:
                return get_booking_space_result

            booking = get_booking_space_result.data['booking']
            space = get_booking_space_result.data['space']

            # 2. 权限验证 (谁可以签到)
            effective_check_in_method = space.effective_check_in_method
            checked_in_by_user = user  # 默认签到人是当前操作用户

            if effective_check_in_method == CHECK_IN_METHOD_NONE:
                return ServiceResult.error_result(
                    message=f"空间 {space.name} 不需要签到。",
                    error_code="check_in_not_required",
                    status_code=400
                )

            # --- 权限和签到执行人确定 ---
            # 判断当前用户是否是预订人本人
            is_user_self_checking_in = (user == booking.user)

            # 签到员 (包括系统管理员和空间管理员)
            can_staff_check_in = user.is_system_admin or user.is_space_manager or \
                                (user.is_check_in_staff and user.has_perm('spaces.can_check_in_real_space', space))

            if is_staff_manual_check_in: # 如果是明确的工作人员手动签到接口调用
                if not can_staff_check_in:
                    return ServiceResult.error_result(
                        message="您没有权限作为工作人员为该预订进行签到。",
                        error_code="staff_check_in_permission_denied",
                        status_code=403
                    )
                # 工作人员代签或手动签到，checked_in_by 记录为当前工作人员
                checked_in_by_user = user

                # 验证空间配置是否允许工作人员签到
                if effective_check_in_method not in [CHECK_IN_METHOD_STAFF, CHECK_IN_METHOD_HYBRID]:
                    return ServiceResult.error_result(
                        message=f"空间 {space.name} 当前签到方式 ({space.get_check_in_method_display()}) 不允许工作人员手动签到。",
                        error_code="staff_manual_check_in_not_allowed_by_space_config",
                        status_code=400
                    )
                # Note: 工作人员手动签到时，通常不强制上传照片或定位，由工作人员自行判断。
                # 如果要求，可以在这里添加 `if not photo: return error`
                # 定位的话，可以记录，但不强制验证是否在范围内 (因为工作人员可能在后台操作或巡检签到)
                # 如果是扫码签到，则客户端应确保传 QR 码数据或 ID。这里假设签到员直接输入 booking_pk。
            elif is_user_self_checking_in: # 预订人本人自行签到
                # 验证空间配置是否允许用户自行签到
                if effective_check_in_method not in [CHECK_IN_METHOD_SELF, CHECK_IN_METHOD_HYBRID,
                                                     CHECK_IN_METHOD_LOCATION]:
                    return ServiceResult.error_result(
                        message=f"空间 {space.name} 当前签到方式 ({space.get_check_in_method_display()}) 不允许您自行签到。",
                        error_code="self_check_in_not_allowed_by_space_config",
                        status_code=400
                    )
                # 强制要求照片和定位 (如果需要)
                if effective_check_in_method in [CHECK_IN_METHOD_SELF, CHECK_IN_METHOD_HYBRID] and not photo:
                    return ServiceResult.error_result(
                        message=f"空间 {space.name} 的自行签到方式需要提供照片作为凭证。",
                        error_code="photo_required_for_self_check_in",
                        status_code=400
                    )
                if effective_check_in_method == CHECK_IN_METHOD_LOCATION:
                    location_validation_result = self._validate_location_check_in(space, latitude, longitude)
                    if not location_validation_result.success:
                        return location_validation_result
            else: # 既不是预订人本人，也不是明确的工作人员手动签到，且没有工作人员权限
                return ServiceResult.error_result(
                    message="您没有权限为他人签到，或该空间不允许自行签到。", # NEW: 优化措辞
                    error_code="unauthorized_check_in_attempt",
                    status_code=403
                )
            # --- 权限和签到执行人确定 END ---

            # 3. 签到时间窗口验证
            check_in_time_result = self._check_check_in_time_window(booking)
            if not check_in_time_result.success:
                return check_in_time_result

            # 4. 确保没有重复签到
            if self._check_in_record_dao.get_record_by_booking_id(booking_pk):
                return ServiceResult.error_result(
                    message="该预订已签到，请勿重复操作。",
                    error_code="already_checked_in",
                    status_code=409
                )

            # 5. 创建签到记录 (check_in_method 记录实际的空间配置方法)
            check_in_record = self._check_in_record_dao.create(
                booking=booking,
                user=booking.user,  # 签到记录的主体用户始终是预订人
                checked_in_by=checked_in_by_user,  # 执行签到操作的用户
                check_in_time=timezone.now(),
                check_in_method=effective_check_in_method, # NEW: 记录空间的有效签到方式
                latitude=latitude,
                longitude=longitude,
                 # photo 字段只有当 client_request_method 是 'PHOTO' 或 (SELF, HYBRID 模式下) 被强制要求时才会有值
                check_in_image=photo,
                notes=notes
            )

            # 6. 更新预订状态为已签到
            # `update` 方法会自动调用 Booking 模型的 save()，并处理二维码等逻辑
            updated_booking = self._booking_dao.update(
                booking,
                status=Booking.BOOKING_STATUS_CHECKED_IN,
                # 记录实际签到时间
                actual_check_in_time=timezone.now()
            )

            if not updated_booking:
                raise InternalServerError("签到后更新预订状态失败。")

            # NEW: 签到成功后，立即强制使该 Booking 对象的详情缓存失效
            CacheService.invalidate_object_cache('bookings:booking', booking_pk)
            logger.info(f"CheckInService: Successfully invalidated cache for bookings:booking:{booking_pk} after check-in.")

            # NEW: 重新获取一次最新的 booking 对象，以确保返回的数据是最新的，
            # 并且其 to_dict() 方法能够反映最新的状态。
            # 这通常会命中数据库，因为我们刚刚使缓存失效了。
            latest_booking = self._booking_dao.get_booking_by_id(booking_pk)
            if not latest_booking:
                # 理论上不会发生，因为刚刚更新了
                logger.error(f"CheckInService: Failed to retrieve latest booking {booking_pk} after successful check-in and cache invalidation.")
                return ServiceResult.error_result(message="签到成功但获取最新预订信息失败。", status_code=500)

            # 7. 返回更新后的预订数据
            return ServiceResult.success_result(
                data=latest_booking.to_dict(), # 返回最新的 booking 字典
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
                       (user.is_check_in_staff and user.has_perm('spaces.can_check_in_real_space', booking.related_space)) # NEW: 签到员可以查看其能签到的空间预订的签到记录

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