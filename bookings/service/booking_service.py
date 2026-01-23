# bookings/service/booking_service.py
import logging
import uuid
from typing import Dict, Any, Optional, Union
from datetime import datetime

from django.db.models import QuerySet, Q
from django.db import transaction  # 确保导入 transaction
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
# 导入通知服务
from notifications.services import NotificationService

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
        self._booking_preliminary_service: Optional[BookingPreliminaryService] = None
        self._notification_service: Optional[NotificationService] = None

    def _get_booking_preliminary_service(self) -> BookingPreliminaryService:
        if self._booking_preliminary_service is None:
            self._booking_preliminary_service = ServiceFactory.get_service('BookingPreliminaryService')
        return self._booking_preliminary_service

    def _get_notification_service(self) -> NotificationService:
        if self._notification_service is None:
            self._notification_service = ServiceFactory.get_service('NotificationService')
        return self._notification_service

    def _send_booking_notification(self, booking_instance: Booking, message_type: str, performed_by_user: CustomUser,
                                   reason: Optional[str] = None):
        """
        Helper to send booking-related notifications.
        This function now uses transaction.on_commit to ensure notifications are
        dispatched only after the main transaction (e.g., booking creation/update) commits.
        Assumes booking_instance has 'user', 'space', 'bookable_amenity__amenity' pre-loaded.
        """
        # 提前检查用户和邮件地址，避免不必要的处理和在 on_commit 中再次检查
        if not booking_instance.user or not booking_instance.user.email:
            logger.warning(
                f"Skipping notification for booking {booking_instance.pk}: User {booking_instance.user.pk if booking_instance.user else 'N/A'} has no email configured (username: {booking_instance.user.username if booking_instance.user else 'N/A'}).")
            return

        # --- 捕获所有必要的原始数据，以避免在 on_commit 中访问可能已失效的对象 ---
        booking_pk = booking_instance.pk
        booking_user_pk = booking_instance.user.pk  # 这是邮件的接收者
        booking_user_email = booking_instance.user.email  # 用于日志记录

        booking_item_name = booking_instance.space.name if booking_instance.space else \
            (
                booking_instance.bookable_amenity.amenity.name if booking_instance.bookable_amenity and booking_instance.bookable_amenity.amenity else "未知预订项目")

        start_time_formatted = booking_instance.start_time.strftime(
            '%Y-%m-%d %H:%M') if booking_instance.start_time else "未知开始时间"
        end_time_formatted = booking_instance.end_time.strftime(
            '%Y-%m-%d %H:%M') if booking_instance.end_time else "未知结束时间"

        # 安全获取预订用户 (接收者) 的全名
        full_name_callable_recipient = getattr(booking_instance.user, 'get_full_name', None)
        if callable(full_name_callable_recipient):
            full_name_recipient = full_name_callable_recipient()
        else:
            full_name_recipient = full_name_callable_recipient
        if not full_name_recipient:
            full_name_recipient = booking_instance.user.username or "用户"

        # 安全获取执行操作的用户 (例如，取消预订的管理员) 的全名和电话
        full_name_callable_performer = getattr(performed_by_user, 'get_full_name', None)
        if callable(full_name_callable_performer):
            full_name_performer = full_name_callable_performer()
        else:
            full_name_performer = full_name_callable_performer
        if not full_name_performer:
            full_name_performer = performed_by_user.username or "操作人"

        performed_by_user_phone = performed_by_user.phone_number if hasattr(performed_by_user,
                                                                            'phone_number') and performed_by_user.phone_number else None
        # --- 原始数据捕获结束 ---

        email_subject = ""
        email_content = ""

        if message_type == 'BOOKING_SUBMITTED':
            email_subject = f"您的预订已成功提交！(ID: {booking_pk})"
            email_content = (
                f"尊敬的 {full_name_recipient}，\n\n"
                f"您的预订请求已成功提交至系统。\n"
                f"预订ID: {booking_pk}\n"
                f"预订项目: {booking_item_name}\n"
                f"开始时间: {start_time_formatted}\n"
                f"结束时间: {end_time_formatted}\n"
                f"当前状态: {booking_instance.get_status_display()}\n\n"
                f"我们将在审核后尽快通知您结果（如果需要审核）。您可以登录系统查看预订详情。\n\n"
                f"此致，\n您的系统团队"
            )
        elif message_type == 'BOOKING_APPROVED':
            email_subject = f"您的预订已成功批准！(ID: {booking_pk})"
            email_content = (
                f"尊敬的 {full_name_recipient}，\n\n"
                f"恭喜！您的预订请求已获得批准！\n"
                f"预订ID: {booking_pk}\n"
                f"预订项目: {booking_item_name}\n"
                f"开始时间: {start_time_formatted}\n"
                f"结束时间: {end_time_formatted}\n"
                f"状态: 已批准\n\n"
                f"请按照预订时间前往使用。如有疑问，请联系管理人员。\n\n"
                f"此致，\n您的系统团队"
            )
        elif message_type == 'BOOKING_REJECTED':
            email_subject = f"您的预订申请已被拒绝 (ID: {booking_pk})"
            email_content = (
                f"尊敬的 {full_name_recipient}，\n\n"
                f"很抱歉通知您，您的预订请求已被拒绝。\n"
                f"预订ID: {booking_pk}\n"
                f"预订项目: {booking_item_name}\n"
                f"开始时间: {start_time_formatted}\n"
                f"结束时间: {end_time_formatted}\n"
                f"状态: 已拒绝\n"
                f"原因: {reason or '管理人员未说明具体原因。'}\n\n"
                f"如有疑问，请咨询相关管理员。\n\n"
                f"此致，\n您的系统团队"
            )
        elif message_type == 'BOOKING_CANCELLED':
            email_subject = f"您的预订已被取消 (ID: {booking_pk})"
            cancellation_reason_input = reason or ''  # 从函数入参接收的理由
            # 判断是预订用户自行取消还是管理员/空间管理员取消
            if performed_by_user.pk == booking_user_pk:  # 预订用户执行的取消
                email_reason_details = (
                    f"原因: {cancellation_reason_input or '您主动取消了预订。'}\n\n"
                )
            else:  # 管理员/空间管理员取消
                admin_action_text = "管理员强制取消"

                admin_contact_info_line = ""
                if performed_by_user_phone:
                    admin_contact_info_line = f"联系电话: {performed_by_user_phone}\n"

                email_reason_details = (
                    f"操作人: {full_name_performer} （管理员）\n"
                    f"操作详情: {admin_action_text}\n"
                    f"{admin_contact_info_line}"  # 添加联系电话
                    f"如有疑问，请咨询相关管理员。\n\n"
                )

            email_content = (
                f"尊敬的 {full_name_recipient}，\n\n"
                f"您的预订请求（ID: {booking_pk}）已被成功取消。\n"
                f"预订项目: {booking_item_name}\n"
                f"开始时间: {start_time_formatted}\n"
                f"结束时间: {end_time_formatted}\n"
                f"状态: 已取消\n"
                f"{email_reason_details}"  # 这里插入构造好的理由详情
                f"此致，\n您的系统团队"
            )
        else:
            logger.warning(
                f"Unknown message type '{message_type}' for booking ID {booking_pk}, skipping notification content generation.")
            return

        # 将实际的通知发送逻辑包装在 transaction.on_commit 中
        # 这个可调用函数只会在当前的事务（无论是创建、取消还是更新）成功提交后执行。
        def _deferred_send_notification_task():
            try:
                # 在 on_commit 任务中重新获取接收者用户对象，以确保它是最新的并对当前事务可见
                try:
                    user_obj_for_notification = CustomUser.objects.get(pk=booking_user_pk)  # Recipient user
                except CustomUser.DoesNotExist:
                    logger.error(
                        f"Cannot re-fetch recipient user {booking_user_pk} for notification for booking {booking_pk} in on_commit task.")
                    return

                notification_service = self._get_notification_service()

                notification_result = notification_service.send_notification(
                    user=user_obj_for_notification,
                    title=email_subject,
                    content=email_content,
                    message_type=message_type
                )
                if not notification_result.success:
                    logger.error(
                        f"Failed to send booking notification for booking ID {booking_pk}, type {message_type} during on_commit execution: {notification_result.message}. Details: {notification_result.errors}")
                else:
                    logger.info(
                        f"Booking notification ({message_type}) dispatched via on_commit for user {booking_user_pk} (email: {booking_user_email}), booking ID {booking_pk}.")
            except Exception as e:
                logger.exception(
                    f"An unexpected error occurred in deferred notification sending for booking ID {booking_pk}, type {message_type}.")

        # 调度 _deferred_send_notification_task 在当前事务提交后运行
        transaction.on_commit(_deferred_send_notification_task)

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

        try:
            # 重新加载 booking_instance，确保所有关联对象都被 select_related 预加载，以便通知内容完整
            booking_instance = self.booking_dao.get_queryset().select_related(
                'user', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
            ).filter(pk=booking_id).first()

            if booking_instance:
                # 预订在此阶段为 SUBMITTED 状态，发送提交通知，执行操作者是预订用户
                self._send_booking_notification(booking_instance, 'BOOKING_SUBMITTED', performed_by_user=user)
            else:
                logger.error(f"Could not fetch booking instance {booking_id} for initial notification after creation.")
        except Exception as e:
            logger.exception(f"Error preparing initial booking submitted notification for booking ID {booking_id}.")

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
        QuerySet[Booking]]:
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
            # 在事务开始时获取预订实例，并确保加载相关数据用于权限检查
            booking = self.booking_dao.get_queryset().select_for_update().select_related(
                'user', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
            ).filter(pk=pk).first()

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

            if booking.status not in [Booking.BOOKING_STATUS_PENDING, Booking.BOOKING_STATUS_APPROVED,
                                      Booking.BOOKING_STATUS_CHECKED_IN]:  # 允许已签到取消
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

            # NEW: 重新加载 booking_instance 以确保所有关联对象都已加载，用于通知
            final_booking_for_notification = self.booking_dao.get_queryset().select_related(
                'user', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
            ).filter(pk=updated_booking.pk).first()

            if final_booking_for_notification:
                # 执行操作者是当前用户
                self._send_booking_notification(final_booking_for_notification, 'BOOKING_CANCELLED', reason=reason,
                                                performed_by_user=user)
            else:
                logger.error(
                    f"Failed to re-fetch booking {updated_booking.pk} for cancellation notification. Notification will not be sent.")
            # END NEW

            return ServiceResult.success_result(message="预订取消成功。")
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"取消预订失败 (ID: {pk}, User: {user.username}).")
            return self._handle_exception(e, default_message="取消预订失败。")

    @transaction.atomic
    def update_booking_status(self, user: CustomUser, pk: int, new_status: str, admin_notes: Optional[str] = None) -> \
            ServiceResult[Booking]:
        try:
            # 在事务开始时获取预订实例，并确保加载相关数据用于权限检查和状态流转
            booking = self.booking_dao.get_queryset().select_for_update().select_related(
                'user', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
            ).filter(pk=pk).first()

            if not booking:
                raise NotFoundException(detail="预订记录未找到。")

            if not user.is_system_admin and \
                    not (user.is_space_manager and booking.related_space and booking.related_space.managed_by == user):
                raise ForbiddenException(detail="您没有权限修改此预订的状态。")

            current_status = booking.status

            valid_statuses = [choice[0] for choice in Booking.BOOKING_STATUS_CHOICES]
            if new_status not in valid_statuses:
                raise BadRequestException(detail=f"无效的预订状态 '{new_status}'。", code="invalid_booking_status")

            # 状态流转规则检查
            if new_status == Booking.BOOKING_STATUS_APPROVED and current_status != Booking.BOOKING_STATUS_PENDING:
                raise BadRequestException(detail="只有待审核状态的预订才能被批准。", code="invalid_status_transition")

            if new_status == Booking.BOOKING_STATUS_REJECTED and current_status != Booking.BOOKING_STATUS_PENDING:
                raise BadRequestException(detail="只有待审核状态的预订才能被拒绝。", code="invalid_status_transition")

            if new_status == Booking.BOOKING_STATUS_CHECKED_IN and current_status != Booking.BOOKING_STATUS_APPROVED:
                raise BadRequestException(detail="只有已批准状态的预订才能签到。", code="invalid_status_transition")

            if new_status == Booking.BOOKING_STATUS_COMPLETED and current_status not in [
                Booking.BOOKING_STATUS_CHECKED_IN, Booking.BOOKING_STATUS_APPROVED]:
                raise BadRequestException(detail="只有已签到或已批准状态的预订才能完成。",
                                          code="invalid_status_transition")

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

            # NEW: 重新加载 booking_instance 以确保所有关联对象都已加载，用于通知
            final_booking_for_notification = self.booking_dao.get_queryset().select_related(
                'user', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
            ).filter(pk=updated_booking.pk).first()

            if final_booking_for_notification:
                # 执行操作者是当前用户
                if new_status == Booking.BOOKING_STATUS_APPROVED:
                    self._send_booking_notification(final_booking_for_notification, 'BOOKING_APPROVED',
                                                    performed_by_user=user)
                elif new_status == Booking.BOOKING_STATUS_REJECTED:
                    # 对于拒绝，admin_notes 可以作为原因
                    self._send_booking_notification(final_booking_for_notification, 'BOOKING_REJECTED',
                                                    reason=admin_notes, performed_by_user=user)
            else:
                logger.error(
                    f"Failed to re-fetch booking {updated_booking.pk} for status update notification. Notification will not be sent.")
            # END NEW

            return ServiceResult.success_result(
                data=updated_booking,
                message="预订状态更新成功。"
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新预订状态失败 (ID: {pk}, User: {user.username}, New Status: {new_status}).")
            return self._handle_exception(e, default_message="更新预订状态失败。")