# bookings/tasks/no_show_tasks.py
import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from core.dao import DAOFactory
from core.service.factory import ServiceFactory
from bookings.models import Booking as BookingModel  # 导入 BookingModel

logger = logging.getLogger(__name__)

_violation_service_instance = None
_booking_dao_instance = None


def get_violation_service_instance():
    """惰性加载 ViolationService 实例。"""
    global _violation_service_instance
    if _violation_service_instance is None:
        _violation_service_instance = ServiceFactory.get_service('ViolationService')
    return _violation_service_instance


def get_booking_dao_instance():
    """惰性加载 BookingDAO 实例。"""
    global _booking_dao_instance
    if _booking_dao_instance is None:
        _booking_dao_instance = DAOFactory.get_dao('booking')
    return _booking_dao_instance


@shared_task(bind=True, max_retries=3, default_retry_delay=60 * 2)  # 2分钟重试间隔
def create_no_show_violation_for_single_booking(self, booking_id: int):
    """
    Celery 任务：处理单个预订的未到场状态检查和违规创建。
    该任务旨在被调度在预订结束时间后短暂延迟执行。
    """
    logger.info(f"Celery Task (ID:{self.request.id}): Checking booking {booking_id} for no-show violation.")

    violation_service = get_violation_service_instance()
    booking_dao = get_booking_dao_instance()

    try:
        # 1. 再次获取预订的最新状态，并预加载相关字段
        booking = booking_dao.get_queryset().select_related(
            'user', 'space__space_type', 'bookable_amenity__space__space_type', 'related_space__space_type'
        ).get(pk=booking_id)

        # 2. 任务执行时重新检查条件，以确保幂等性和正确性
        now = timezone.now()

        if booking.status == BookingModel.BOOKING_STATUS_NO_SHOW:
            logger.info(f"Booking {booking.pk} is already NO_SHOW, skipping violation creation for {now}.")
            return

        if booking.status not in [BookingModel.BOOKING_STATUS_APPROVED]:
            logger.warning(
                f"Booking {booking.pk} (status: {booking.status}) is no longer PENDING or APPROVED. "
                "可能在任务调度后被签到、拒绝或取消。跳过处理。"
            )
            return

        if booking.end_time >= now:
            logger.warning(
                f"Booking {booking.pk} (end_time: {booking.end_time}) 尚未过期 ({now}). "
                "此任务可能被过早触发或时间漂移。跳过处理。"
            )
            return

        # 3. 调用服务层内部方法，执行实际的未到场标记和违规创建
        service_result = violation_service._create_no_show_violation_for_booking(booking=booking, issued_by_user=None)

        if service_result.success:
            logger.info(f"Successfully processed booking {booking.pk} as NO_SHOW and created violation immediately.")
        else:
            logger.warning(
                f"Failed to process booking {booking.pk} for immediate NO_SHOW: {service_result.message}. "
                "每日的批量任务将作为补充和兜底。"
            )
            # 如果是可重试的瞬时错误，抛出异常让 Celery 自动重试
            if service_result.error_code in ["internal_server_error"]:  # 示例：如果是内部错误可能值得重试
                raise RuntimeError(service_result.message)

    except BookingModel.DoesNotExist:
        logger.warning(
            f"Booking {booking_id} not found when trying to create no-show violation. It might have been deleted.")
    except Exception as e:
        logger.exception(
            f"Error in create_no_show_violation_for_single_booking for booking {booking_id} (ID:{self.request.id}). Retrying...")
        raise self.retry(exc=e)  # 允许 Celery 处理重试


@shared_task(bind=True, max_retries=3)
def process_overdue_approved_bookings_for_no_show(self):
    """
    Celery 定时任务：扫描所有已批准但已过期且未签到的预订，将其标记为 NO_SHOW 并创建违规记录。
    此任务作为兜底机制，以防个别一次性调度的任务失败或被错过。
    """
    logger.info(f"Celery Beat Task (ID:{self.request.id}): Starting to process overdue approved bookings for no-show.")
    violation_service = get_violation_service_instance()
    booking_dao = get_booking_dao_instance()

    now = timezone.now()

    # 获取所有状态为 APPROVED 或 PENDING 且 end_time 已过的预订
    # 同时预加载相关字段，以减少后续循环中的数据库查询
    overdue_approved_bookings = booking_dao.get_queryset().select_related(
        'user', 'space__space_type', 'bookable_amenity__space__space_type', 'related_space__space_type'
    ).filter(
        status__in=[BookingModel.BOOKING_STATUS_APPROVED],
        end_time__lt=now
    )

    processed_count = 0
    violation_created_count = 0

    with transaction.atomic():
        for booking in overdue_approved_bookings:
            try:
                # 调用 ViolationService 中的内部方法来处理单个预订的未到场逻辑
                # 这个内部方法会负责更新预订状态和创建 Violation 记录
                service_result = violation_service._create_no_show_violation_for_booking(
                    booking=booking,
                    issued_by_user=None  # 系统自动触发，没有特定操作人员
                )

                if service_result.success:
                    processed_count += 1
                    violation_created_count += 1
                else:
                    logger.warning(
                        f"Skipping booking {booking.pk} for no-show due to service error: {service_result.message}. "
                        "该预订可能已被处理或不满足未到场条件。"
                    )
            except Exception as e:
                logger.error(f"Error processing booking {booking.pk} for no-show in batch task: {e}", exc_info=True)
                # 不阻断整个批处理，记录错误并继续处理下一个

    logger.info(
        f"Celery Beat Task (ID:{self.request.id}): Finished processing. "
        f"Marked {processed_count} bookings as NO_SHOW and created {violation_created_count} violation records."
    )