# bookings/tasks/booking_tasks.py
import logging
from celery import shared_task
from django.utils import timezone
from django.db import transaction  # 异步任务中也可能需要事务

from core.service.factory import ServiceFactory
from core.service.cache import CacheService  # 从 core/service/cache.py 导入
from bookings.models import Booking as BookingModel  # 使用别名以避免与 Service 类名冲突
from bookings.dao.booking_dao import BookingDAO  # 直接导入 DAO 供任务内部使用

logger = logging.getLogger(__name__)


# BookingValidationCreationService 将在 Task 2.7 中实现
# 这里的 ServiceFactory.get_service 会惰性加载，所以在任务执行时它将会是可用的
# from bookings.service.booking_validation_creation_service import BookingValidationCreationService

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_booking_creation_task(self, booking_id: int):
    """
    Celery 任务：对预订进行深度校验并尝试创建或批准。
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
        else:
            logger.warning(f"Deep validation failed for booking ID: {booking_id}. Errors: {validation_result.errors}")
            # 如果深度校验失败，尝试更新 Booking 的 admin_notes
            booking = booking_dao_instance.get_booking_by_id(booking_id)
            if booking:
                admin_notes_msg = f"深层校验失败: {'; '.join(validation_result.errors)}"
                # 使用 DAO 方法更新状态
                booking_dao_instance.update_booking_processing_status(
                    booking,
                    BookingModel.PROCESSING_STATUS_CHOICES[3][0],  # FAILED_VALIDATION
                    admin_notes=admin_notes_msg,
                    new_booking_status=BookingModel.BOOKING_STATUS_CHOICES[2][0]  # REJECTED
                )
            else:
                logger.error(f"Booking ID {booking_id} not found after deep validation failure, cannot update status.")

    except Exception as e:
        logger.exception(f"Unhandled error in process_booking_creation_task for booking ID: {booking_id}. Retrying...")
        # 捕获任何异常，并尝试重试
        with transaction.atomic():  # 确保异常处理和状态更新是原子性的
            booking = booking_dao_instance.get_booking_by_id(booking_id)  # 重新获取，可能需要锁
            if booking:
                booking_dao_instance.update_booking_processing_status(
                    booking,
                    BookingModel.PROCESSING_STATUS_CHOICES[4][0],  # FAILED_RUNTIME
                    admin_notes=f"深层校验运行时错误: {str(e)}",
                    new_booking_status=BookingModel.BOOKING_STATUS_CHOICES[2][0]  # REJECTED
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
                    admin_notes_msg = f"深层校验运行时错误（重试超出）：{str(e)}"
                    booking_dao_instance.update_booking_processing_status(
                        booking,
                        BookingModel.PROCESSING_STATUS_CHOICES[4][0],  # FAILED_RUNTIME
                        admin_notes=admin_notes_msg,
                        new_booking_status=BookingModel.BOOKING_STATUS_CHOICES[2][0]  # REJECTED
                    )
                else:
                    logger.error(f"Booking ID {booking_id} not found after max retries exceeded, cannot update status.")
    finally:
        # 无论成功或失败，最后都 dispatch post_booking_actions_task
        logger.info(f"Dispatching post-booking actions for booking ID: {booking_id}...")
        post_booking_actions_task.delay(booking_id)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def post_booking_actions_task(self, booking_id: int):
    """
    Celery 任务：预订创建或验证后的后处理（通知、缓存失效等）。
    """
    booking_dao_instance = BookingDAO()  # 获取 DAO 实例
    try:
        booking = booking_dao_instance.get_booking_by_id(booking_id)
        if not booking:
            logger.error(f"Post-booking actions: Booking ID {booking_id} not found.")
            return

        logger.info(
            f"Starting post-booking actions for booking ID: {booking_id} with processing status {booking.processing_status}.")

        if booking.processing_status == BookingModel.PROCESSING_STATUS_CHOICES[2][0]:  # 'CREATED'
            # 预订成功创建/批准
            # 发送预订确认通知 (委托 Notification Service 或另一个 Celery 任务)
            # notification_service.send_booking_confirmation(booking) # 占位符
            logger.info(
                f"Booking {booking_id} successfully created/approved. Sending confirmation notification (placeholder).")

            # 使此特定预订的详情缓存失效
            CacheService.invalidate_object_cache('bookings:booking', booking_id)
            logger.info(f"Invalidated object cache for bookings:booking:{booking_id}.")

            # 使相关的列表缓存失效 (例如，用户的预订列表、空间的预订列表)
            # 这里采取相对粗粒度的失效策略，更精细的方式需要具体用户ID和空间ID
            CacheService.delete_many_by_prefix('bookings:booking:list_by_user')  # 包含所有用户列表，或者更细到特定user_pk
            CacheService.delete_many_by_prefix('bookings:booking:list_active')
            CacheService.delete_many_by_prefix('spaces:space')  # 可能会影响空间可用性显示，所以整个 Space 相关的缓存都可以清掉
            CacheService.delete_many_by_prefix('spaces:bookable_amenity')  # 如果设施列表也显示可用性

            logger.info(f"Invalidated broad list caches for bookings and spaces/amenities.")

        elif booking.processing_status in [
            BookingModel.PROCESSING_STATUS_CHOICES[3][0],  # 'FAILED_VALIDATION'
            BookingModel.PROCESSING_STATUS_CHOICES[4][0]  # 'FAILED_RUNTIME'
        ]:
            # 预订校验失败或运行时错误
            # 发送预订失败通知 (占位符)
            # notification_service.send_booking_failure(booking)
            logger.warning(
                f"Booking {booking_id} with processing status {booking.processing_status} failed. Sending failure notification (placeholder). Admin notes: {booking.admin_notes}")

            # 清除 Redis 中的瞬时状态缓存 (如果有的话) - 例如临时锁
            # 例如：CacheService.delete('bookings:temp_lock', identifier=booking_id)
            logger.debug(f"Cleared transient state for booking {booking_id} (placeholder).")

            # 同样使此失败预订的详情缓存失效，以防它被临时缓存
            CacheService.invalidate_object_cache('bookings:booking', booking_id)

    except Exception as e:
        logger.exception(f"Unhandled error in post_booking_actions_task for booking ID: {booking_id}. Retrying...")
        try:
            self.retry(exc=e)
        except self.MaxRetriesExceededError:
            logger.error(
                f"Max retries exceeded for post_booking_actions_task for booking ID: {booking_id}. Manual intervention might be needed.")