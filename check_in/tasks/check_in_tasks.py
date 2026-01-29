# check_in/tasks/check_in_tasks.py
import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from core.dao import DAOFactory
from bookings.models import Booking as BookingModel  # 从 bookings.models 导入 BookingModel
from check_in.service.check_in_service import CHECK_IN_GRACE_PERIOD_MINUTES # 导入缓冲时间配置

logger = logging.getLogger(__name__)

# 使用全局变量缓存 DAO 实例，避免在每次任务触发时重复创建
_booking_dao_instance = None

def get_booking_dao_instance():
    """惰性加载 BookingDAO 实例。"""
    global _booking_dao_instance
    if _booking_dao_instance is None:
        _booking_dao_instance = DAOFactory.get_dao('booking')
    return _booking_dao_instance

@shared_task(bind=True, max_retries=3, default_retry_delay=60 * 5) # 5分钟重试间隔
def finalize_checked_in_bookings_task(self):
    """
    Celery 定时任务：扫描所有已签到但已过期的预订，将其状态更新为 COMPLETED。
    每六小时运行一次。
    """
    logger.info(f"Celery Beat Task (ID:{self.request.id}): Starting to finalize checked-in bookings.")

    booking_dao = get_booking_dao_instance()
    now = timezone.now()

    # 找出所有状态为 CHECKED_IN 且距离预订结束时间已经超过缓冲期的预订
    # 这里我们使用 CHECK_IN_GRACE_PERIOD_MINUTES 作为缓冲时间，允许预订结束后一段时间内仍为 CHECKED_IN
    overdue_checked_in_bookings = booking_dao.get_queryset().filter(
        status=BookingModel.BOOKING_STATUS_CHECKED_IN,
        end_time__lt=now - timedelta(minutes=CHECK_IN_GRACE_PERIOD_MINUTES) # 结束时间 + 缓冲时间 < 当前时间
    )

    processed_count = 0
    with transaction.atomic():
        for booking in overdue_checked_in_bookings:
            try:
                # 更新预订状态为 COMPLETED
                update_fields = {'status': BookingModel.BOOKING_STATUS_COMPLETED}
                admin_notes_entry = f"\n[{now.strftime('%Y-%m-%d %H:%M')}] 系统自动标记为 [已完成]。原因：预订已签到且已过期 {CHECK_IN_GRACE_PERIOD_MINUTES} 分钟。"
                update_fields['admin_notes'] = (booking.admin_notes or '') + admin_notes_entry

                updated_booking = booking_dao.update(booking, **update_fields) # 使用 booking 实例进行更新
                if updated_booking:
                    processed_count += 1
                    logger.info(f"Booking {booking.pk} status updated from CHECKED_IN to COMPLETED.")
                else:
                    logger.warning(f"Failed to update booking {booking.pk} status to COMPLETED.")

            except Exception as e:
                logger.error(
                    f"Error processing checked-in booking {booking.pk} for completion in batch task: {e}",
                    exc_info=True
                )
                # 不阻断整个批处理，记录错误并继续处理下一个

    logger.info(
        f"Celery Beat Task (ID:{self.request.id}): Finished finalizing. "
        f"Updated {processed_count} checked-in bookings to COMPLETED status."
    )