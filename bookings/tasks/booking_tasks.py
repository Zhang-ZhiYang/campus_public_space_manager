# bookings/tasks/booking_tasks.py
import logging
from celery import shared_task
from django.contrib.auth.models import Group
from django.utils import timezone
from django.db import transaction  # 异步任务中也可能需要事务
from typing import Optional, List, Union

from core.service.factory import ServiceFactory
from core.service.cache import CacheService  # 从 core/service/cache.py 导入
from bookings.models import Booking as BookingModel, Space as SpaceModel  # 导入 BookingModel 和 SpaceModel
from bookings.dao.booking_dao import BookingDAO  # 直接导入 DAO 供任务内部使用
from users.models import CustomUser

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_booking_creation_task(self, booking_id: int):
    """
    Celery 任务：对预订进行深度校验并尝试创建或批准。
    此任务不再直接处理缓存失效，而是依赖模型信号和 `booking_cache_invalidation_task`。
    """
    booking_validation_service = ServiceFactory.get_service('BookingValidationCreationService')  # 获取 Service 实例
    booking_dao_instance = BookingDAO()  # 获取 DAO 实例

    try:
        logger.info(f"Starting deep validation for booking ID: {booking_id}...")

        # 调用 BookingValidationCreationService 进行深度校验和确认
        validation_result = booking_validation_service.deep_validate_and_confirm(booking_id)

        if validation_result.success:
            logger.info(
                f"Deep validation successful for booking ID: {booking_id}. Final status: {validation_result.data.status}.")
            # 成功创建/批准后，信号会处理缓存失效
        else:
            logger.warning(f"Deep validation failed for booking ID: {booking_id}. Errors: {validation_result.errors}")
            # 如果深度校验失败，尝试更新 Booking 的 admin_notes (此更新也会触发信号)
            booking = booking_dao_instance.get_booking_by_id(booking_id)
            if booking:
                admin_notes_msg = f"深层校验失败: {'; '.join(validation_result.errors if validation_result.errors else ['未知错误'])}"
                # 修正: 确保update_booking_processing_status总是接收整数pk作为第一个参数
                # 此 DAO 操作会触发 Booking 模型的 save() 方法，进而触发信号。
                booking_dao_instance.update_booking_processing_status(
                    booking.pk,  # <--- 修正点
                    BookingModel.PROCESSING_STATUS_FAILED_VALIDATION,
                    admin_notes=admin_notes_msg,
                    new_booking_status=BookingModel.BOOKING_STATUS_REJECTED
                )
            else:
                logger.error(f"Booking ID {booking_id} not found after deep validation failure, cannot update status.")

    except Exception as e:
        logger.exception(f"Unhandled error in process_booking_creation_task for booking ID: {booking_id}. Retrying...")
        # 捕获任何异常，并尝试重试
        with transaction.atomic():  # 确保异常处理和状态更新是原子性的
            booking = booking_dao_instance.get_booking_by_id(booking_id)  # 重新获取，可能需要锁
            if booking:
                # 此 DAO 操作会触发 Booking 模型的 save() 方法，进而触发信号。
                booking_dao_instance.update_booking_processing_status(
                    booking.pk,  # <--- 修正点
                    BookingModel.PROCESSING_STATUS_FAILED_RUNTIME,
                    admin_notes=f"深层校验运行时错误: {str(e)}",
                    new_booking_status=BookingModel.BOOKING_STATUS_REJECTED
                )
            else:
                logger.error(f"Booking ID {booking_id} not found during retry error handling, cannot update status.")

        try:
            self.retry(exc=e)  # 重试任务
        except self.MaxRetriesExceededError:
            logger.error(f"Max retries exceeded for booking ID: {booking_id}. Marking as FAILED_RUNTIME (final).")
            # 如果重试次数用尽，最终将状态标记为 FAILED_RUNTIME
            with transaction.atomic():
                booking = booking_dao_instance.get_booking_by_id(booking_id)
                if booking:
                    # 此 DAO 操作会触发 Booking 模型的 save() 方法，进而触发信号。
                    booking_dao_instance.update_booking_processing_status(
                        booking.pk,  # <--- 修正点
                        BookingModel.PROCESSING_STATUS_FAILED_RUNTIME,
                        admin_notes=admin_notes_msg,
                        new_booking_status=BookingModel.BOOKING_STATUS_REJECTED
                    )
                else:
                    logger.error(f"Booking ID {booking_id} not found after max retries exceeded, cannot update status.")
    finally:
        # 无论成功或失败，最后都 dispatch post_booking_actions_task
        # 注意：此处不再是 `post_booking_actions_task`，因为缓存失效主要由信号处理。
        # 如果 `post_booking_actions_task` 还有其他非缓存的业务逻辑（如发送邮件），则保留。
        # 为保持一致性，如果 `post_booking_actions_task` 仅用于缓存，可以移除此处的调用。
        # 为简单起见，如果其包含非缓存逻辑，暂时保留。如果仅是缓存则可以移除并依赖信号。
        pass  # 缓存失效现在由信号处理，后续动作可在此加入或移除此finally块


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def booking_cache_invalidation_task(self,
                                    booking_pk: int,
                                    is_deleted_event: bool = False,
                                    old_related_space_id: Optional[int] = None,
                                    current_related_space_id: Optional[int] = None,
                                    needs_broad_invalidation: bool = False):
    """
    Celery 任务：用于在 Booking 模型保存或删除后，异步处理相关缓存的失效。
    此任务将更细粒度地处理不同类型的缓存。
    """
    logger.info(
        f"Booking Cache Invalidation Task (ID:{self.request.id}): Processing for Booking PK={booking_pk}, is_deleted={is_deleted_event}")

    # 1. 使单个 Booking 对象的详情缓存失效 (总是执行)
    CacheService.invalidate_object_cache('bookings:booking', booking_pk)
    logger.info(f"Invalidated object cache for bookings:booking:{booking_pk}.")

    # 2. 处理与相关联 Space(或设施的父空间) 相关的缓存
    affected_space_pks = set()
    if old_related_space_id:  # 如果旧有关联空间 (更新或删除)
        affected_space_pks.add(old_related_space_id)
    if current_related_space_id:  # 如果新有关联空间 (创建或更新)
        affected_space_pks.add(current_related_space_id)

    # 如果是删除事件，或者需要广泛失效
    if is_deleted_event or needs_broad_invalidation:
        # 清除用户相关的预订列表缓存 (可能对用户每日限制等有用)
        # 注意: 这里的 user_pk 需要从 booking_pk 对应的 Booking 对象重新获取，
        # 如果是删除事件，booking_pk 可能已不存在，需要更谨慎。
        # Simple for now: just clear all/prefix.
        CacheService.delete_many_by_prefix('bookings:booking:list_by_user')
        CacheService.delete_many_by_prefix('bookings:booking:list_active')
        logger.info(f"Invalidated broad list caches for bookings (list_by_user, list_active).")

        # 清除所有 Space 列表缓存 (因为 Space 的占用状态变化会影响列表的过滤或显示)
        CacheService.delete_many_by_prefix('spaces:space')  # 包括 detail, list_all, list_by_parent, list_filtered等
        CacheService.delete_many_by_prefix('spaces:bookable_amenity')  # 包含 bookable_amenity 列表
        logger.info(f"Invalidated broad caches for spaces and bookable amenities.")

        # If it was a deletion and `booking_pk` is truly gone, only rely on general invalidation.
        # Otherwise, if we can still fetch the booking or its related space is known, use it.
        if affected_space_pks:
            for spk in affected_space_pks:
                # 精确失效指定 Space 的详情和 BookableAmenity 列表，提高效率
                CacheService.invalidate_object_cache('spaces:space', spk)
                CacheService.invalidate_list_cache('spaces:bookable_amenity', custom_postfix=f'list_by_space:{spk}')
                logger.info(f"Invalidated spaces:space:{spk} and spaces:bookable_amenity:list_by_space:{spk} caches.")

                # 如果这个 space 本身是一个子空间，也需要让其父空间的子空间列表缓存失效
                try:
                    current_space: Optional[SpaceModel] = SpaceModel.objects.filter(pk=spk).first()
                    if current_space and current_space.parent_space_id:
                        CacheService.invalidate_list_cache('spaces:space',
                                                           custom_postfix=f'list_by_parent:{current_space.parent_space_id}')
                        logger.info(
                            f"Invalidated spaces:space:list_by_parent:{current_space.parent_space_id} due to booking affecting child space {spk}.")
                except Exception as e:
                    logger.warning(f"Failed to get parent_space for space {spk} during cache invalidation: {e}")

    logger.info(f"Booking Cache Invalidation Task (ID:{self.request.id}) completed for Booking PK={booking_pk}.")

@shared_task(bind=True, max_retries=3, default_retry_delay=60 * 5)
def reject_overdue_pending_bookings_task(self):
    """
    Celery 任务： 定期查找已过时但仍处于待审核状态的预订，并将其拒绝。
    一个预订被认为是“过时未处理”：
    1. 预订的开始时间 (`start_time`) 早于当前时间。
    2. 预订的业务状态 (`status`) 仍为 'PENDING'。
    3. 预订的处理状态 (`processing_status`) 并非已明确失败且已拒绝，即仍在等待处理或审批。
    """
    logger.info("Starting reject_overdue_pending_bookings_task...")
    booking_dao = BookingDAO()
    booking_service = ServiceFactory.get_service('BookingService')

    try:
        # 【修改点开始】获取一个系统用户来执行拒绝操作
        # 遵循 'is_superuser 或 CustomUser 属于 "系统管理员" 用户组' 的逻辑
        system_user: Optional[CustomUser] = None

        # 尝试查找属于 "系统管理员" 组的用户
        try:
            admin_group = Group.objects.get(name="系统管理员")
            system_user = CustomUser.objects.filter(groups=admin_group, is_active=True).first()
        except Group.DoesNotExist:
            logger.warning("Group '系统管理员' does not exist. Falling back to is_superuser check.")

        # 如果没有找到 "系统管理员" 组的用户，尝试查找 is_superuser=True 的用户
        if not system_user:
            system_user = CustomUser.objects.filter(is_superuser=True, is_active=True).first()

        if not system_user:
            logger.error("No active system admin or superuser found to perform overdue booking rejections. Task aborted.")
            return # 任务中止，因为缺少执行者
        # 【修改点结束】

        # 查找符合条件的预订：
        overdue_bookings = booking_dao.get_queryset().filter(
            start_time__lt=timezone.now(),
            status=BookingModel.BOOKING_STATUS_PENDING,
            processing_status__in=[
                BookingModel.PROCESSING_STATUS_SUBMITTED,
                BookingModel.PROCESSING_STATUS_IN_PROGRESS,
                BookingModel.PROCESSING_STATUS_CREATED
            ]
        ).select_related('user', 'related_space')

        if not overdue_bookings.exists():
            logger.info("No overdue pending bookings found for rejection.")
            return

        rejected_count = 0
        for booking in overdue_bookings:
            try:
                with transaction.atomic():
                    reject_reason = f"预订开始时间已过，系统自动拒绝 (原状态: {booking.get_status_display()})"
                    service_result = booking_service.update_booking_status(
                        user=system_user,
                        pk=booking.pk,
                        new_status=BookingModel.BOOKING_STATUS_REJECTED,
                        admin_notes=reject_reason
                    )

                    if service_result.success:
                        rejected_count += 1
                        logger.info(f"Successfully rejected overdue booking {booking.pk}. Reason: {reject_reason}")
                    else:
                        logger.warning(
                            f"Failed to reject overdue booking {booking.pk}. Reason: {service_result.message}. Details: {service_result.errors}")

            except Exception as e:
                logger.error(
                    f"Error processing overdue booking {booking.pk} for rejection in batch task: {e}",
                    exc_info=True
                )

        logger.info(f"Finished reject_overdue_pending_bookings_task. Rejected {rejected_count} bookings.")

    except Exception as e:
        logger.exception("An unexpected error occurred in reject_overdue_pending_bookings_task. Retrying...")
        self.retry(exc=e)