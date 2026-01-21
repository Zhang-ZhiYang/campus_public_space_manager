# bookings/service/booking_service.py
import logging
import uuid
from typing import Dict, Any, Optional, Union
from datetime import datetime

from django.db.models import QuerySet, Q
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework import status as http_status

from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.service.factory import ServiceFactory
from core.service.cache import CacheService
from core.utils.exceptions import NotFoundException, ForbiddenException, BadRequestException, ConflictException, \
    ServiceException, InternalServerError, CustomAPIException

from users.models import CustomUser
from spaces.models import Space, BookableAmenity
from bookings.models import Booking  # 直接导入 Booking 模型

# 导入异步任务
from bookings.tasks import booking_tasks
# 导入初步校验服务
from bookings.service.booking_preliminary_service import BookingPreliminaryService

logger = logging.getLogger(__name__)

class BookingService(BaseService):
    """
    负责处理 Booking 模型相关的核心业务逻辑。
    作为 API 层调用异步预订创建的入口。
    """
    _dao_map = {
        'booking_dao': 'booking',
        'space_dao': 'space',
        'bookable_amenity_dao': 'bookable_amenity',
    }

    # DAO 层的预加载设置，可以根据具体方法需求调整
    _allowed_prefetch_related = ['user', 'space', 'bookable_amenity', 'related_space', 'reviewed_by']
    _allowed_select_related = ['user', 'space__space_type', 'bookable_amenity__amenity',
                               'bookable_amenity__space__space_type', 'related_space__space_type', 'reviewed_by']

    def __init__(self):
        super().__init__()
        self.booking_dao = self._get_dao_instance('booking')
        self._booking_preliminary_service = None

    def _get_booking_preliminary_service(self) -> BookingPreliminaryService:
        if self._booking_preliminary_service is None:
            self._booking_preliminary_service = ServiceFactory.get_service('BookingPreliminaryService')
        return self._booking_preliminary_service

    def create_booking(self, user: CustomUser, request_data: Dict[str, Any]) -> ServiceResult[Dict[str, Any]]:
        logger.info(f"Received booking creation request for user {user.pk} with data: {request_data}")
        preliminary_service = self._get_booking_preliminary_service()

        pre_validate_result = preliminary_service.pre_validate(user, request_data)

        if not pre_validate_result.success:
            logger.warning(f"Preliminary validation failed: {pre_validate_result.message}")
            return pre_validate_result

        booking_id, target_space, target_amenity = pre_validate_result.data

        if target_space is None and target_amenity is None:
            logger.info(f"Idempotent request received. Returning existing booking ID {booking_id}.")
            return pre_validate_result

        logger.info(
            f"Booking ID {booking_id} created in SUBMITTED state and deep validation task dispatched by preliminary service.")
        return pre_validate_result

    @CacheService.cache_method(key_prefix='bookings:booking')
    def get_booking(self, user: CustomUser, pk: int) -> ServiceResult[Dict[str, Any]]:
        try:
            booking = self.booking_dao.get_booking_by_id(pk)
            if not booking:
                raise NotFoundException(detail="预订记录未找到。")

            if booking.user.pk != user.pk and \
                    not user.is_system_admin and \
                    not (user.is_space_manager and booking.related_space and booking.related_space.managed_by == user):
                raise ForbiddenException(detail="您没有权限查看此预订记录。")

            booking_dict = booking.to_dict(include_related=True)
            return ServiceResult.success_result(data=booking_dict)
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"获取预订详情失败 (ID: {pk}, User: {user.username}).")
            return self._handle_exception(e, default_message="获取预订详情失败。")

    def get_all_bookings(self, user: CustomUser, filters: Optional[Dict[str, Any]] = None) -> ServiceResult[
        QuerySet[Booking]]:  # 类型提示使用 Booking
        try:
            base_filters = Q(user=user)

            is_admin_or_space_manager = user.is_system_admin or user.is_space_manager

            if is_admin_or_space_manager:
                if user.is_system_admin:
                    base_filters = Q()
                elif user.is_space_manager:
                    managed_space_ids = user.managed_spaces.values_list('pk', flat=True)
                    base_filters |= Q(related_space__in=managed_space_ids)

            queryset = self.booking_dao.get_all_bookings(
                filter_conditions=base_filters,
                filters=filters,
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )
            return ServiceResult.success_result(data=queryset)
        except Exception as e:
            logger.exception(f"获取预订列表失败 (User: {user.username}, Filters: {filters}).")
            return self._handle_exception(e, default_message="获取预订列表失败。")

    @transaction.atomic
    def cancel_booking(self, user: CustomUser, pk: int, reason: str) -> ServiceResult[None]:
        try:
            booking = self.booking_dao.get_booking_by_id(pk)
            if not booking:
                raise NotFoundException(detail="预订记录未找到。")

            can_cancel = False
            if booking.user.pk == user.pk:
                can_cancel = True
            elif user.is_system_admin:
                can_cancel = True
            elif user.is_space_manager and booking.related_space and booking.related_space.managed_by == user:
                can_cancel = True

            if not can_cancel:
                raise ForbiddenException(detail="您没有权限取消此预订。")

            # 确保使用正确的 常量名称
            # 这里的 Booking.BOOKING_STATUS_CHECKED_IN 仍然未在你的 models.py 中定义，已根据之前提供的内容移除
            if booking.status not in [Booking.BOOKING_STATUS_PENDING, Booking.BOOKING_STATUS_APPROVED]:
                raise BadRequestException(detail=f"当前预订状态 '{booking.get_status_display()}' 不允许取消。",
                                          code="invalid_status_for_cancel")

            updated_booking = self.booking_dao.update_booking_status(
                pk,
                new_status=Booking.BOOKING_STATUS_CANCELLED,
                admin_notes=f"被 {user.username} 取消，原因: {reason}",
                admin_user=user
            )

            if not updated_booking:
                raise InternalServerError("取消预订过程中DAO未返回有效预订或预订已不存在。")

            CacheService.invalidate_object_cache('bookings:booking', booking.pk)
            CacheService.delete_many_by_prefix('bookings:booking:list_by_user')
            CacheService.delete_many_by_prefix('bookings:booking:list_active')
            CacheService.delete_many_by_prefix('spaces:space')

            logger.info(f"Booking {pk} cancelled by user {user.pk}.")
            return ServiceResult.success_result(message="预订取消成功。")
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"取消预订失败 (ID: {pk}, User: {user.username}).")
            return self._handle_exception(e, default_message="取消预订失败。")

    @transaction.atomic
    def update_booking_status(self, user: CustomUser, pk: int, new_status: str, admin_notes: Optional[str] = None) -> \
            ServiceResult[Booking]:  # 返回类型现在是 Booking 模型实例
        try:
            booking = self.booking_dao.get_booking_by_id(pk)
            if not booking:
                raise NotFoundException(detail="预订记录未找到。")

            if not user.is_system_admin and \
                    not (user.is_space_manager and booking.related_space and booking.related_space.managed_by == user):
                raise ForbiddenException(detail="您没有权限修改此预订的状态。")

            # --- 修正点：将 Booking.STATUS_CHOICES 改为 Booking.BOOKING_STATUS_CHOICES ---
            valid_statuses = [choice[0] for choice in Booking.BOOKING_STATUS_CHOICES]
            if new_status not in valid_statuses:
                raise BadRequestException(detail=f"无效的预订状态 '{new_status}'。", code="invalid_booking_status")

            # 状态流转规则检查 (请确保在 Booking 模型中定义的常量名称与此一致)
            if new_status == Booking.BOOKING_STATUS_APPROVED and booking.status != Booking.BOOKING_STATUS_PENDING:
                raise BadRequestException(detail="只有待审核状态的预订才能被批准。", code="invalid_status_transition")

            if new_status == Booking.BOOKING_STATUS_REJECTED and booking.status != Booking.BOOKING_STATUS_PENDING:
                raise BadRequestException(detail="只有待审核状态的预订才能被拒绝。", code="invalid_status_transition")

            # 根据你提供的 bookings/models.py，这些常量存在
            if new_status == Booking.BOOKING_STATUS_CHECKED_IN and booking.status != Booking.BOOKING_STATUS_APPROVED:
                raise BadRequestException(detail="只有已批准状态的预订才能签到。", code="invalid_status_transition")

            if new_status == Booking.BOOKING_STATUS_COMPLETED and booking.status not in [Booking.BOOKING_STATUS_CHECKED_IN, Booking.BOOKING_STATUS_APPROVED]: # Completed usually from checked-in, but sometimes directly from approved depending on use case
                raise BadRequestException(detail="只有已签到或已批准状态的预订才能完成。", code="invalid_status_transition")

            # --- 修正点：传递 pk 而不是 booking 实例 ---
            updated_booking = self.booking_dao.update_booking_status(
                pk,
                new_status=new_status,
                admin_notes=f"由 {user.username} 更新状态为 {new_status}。" + (
                    f" 备注: {admin_notes}" if admin_notes else ""),
                admin_user=user
            )

            if not updated_booking:
                raise InternalServerError("更新预订状态过程中DAO未返回有效预订或预订已不存在。")

            CacheService.invalidate_object_cache('bookings:booking', booking.pk)
            CacheService.delete_many_by_prefix('bookings:booking:list_by_user')
            CacheService.delete_many_by_prefix('bookings:booking:list_active')
            CacheService.delete_many_by_prefix('spaces:space')

            logger.info(f"Booking {pk} status updated to {new_status} by user {user.pk}.")

            return ServiceResult.success_result(
                data=updated_booking,
                message="预订状态更新成功。"
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新预订状态失败 (ID: {pk}, User: {user.username}, New Status: {new_status}).")
            return self._handle_exception(e, default_message="更新预订状态失败。")