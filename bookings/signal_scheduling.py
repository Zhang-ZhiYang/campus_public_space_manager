# bookings/signals_scheduling.py
import logging
from datetime import timedelta
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from bookings.models import Booking
from bookings.tasks.no_show_tasks import create_no_show_violation_for_single_booking

logger = logging.getLogger(__name__)

# 在预订结束时间后多少分钟触发未到场检查任务
NO_SHOW_TASK_BUFFER_MINUTES = 1


@receiver(post_save, sender=Booking)
def schedule_no_show_check_on_booking_save(sender, instance, created, **kwargs):
    """
    监听 Booking 模型的 post_save 信号。
    当预订状态为 APPROVED 且结束时间在未来时，调度一个 Celery 任务，
    在预订结束时间后 N 分钟检查未到场情况并创建违规记录。
    """

    # 打印instance的当前状态和end_time，用于调试
    logger.debug(f"Booking {instance.pk} post_save detected. Status: {instance.status}, End Time: {instance.end_time}")

    # 1. 仅对状态为 APPROVED 且结束时间在未来的预订进行调度
    # 注意：这里我们仅在预订被“创建”或“状态变为APPROVED”且end_time在未来时调度任务。
    # 如果预订的end_time被更新，Celery会再次调度一个新任务，但由于任务内部的幂等性检查，不会有重复处理的问题。
    if instance.status == Booking.BOOKING_STATUS_APPROVED and instance.end_time > timezone.now():
        schedule_time = instance.end_time + timedelta(minutes=NO_SHOW_TASK_BUFFER_MINUTES)

        # 确保调度时间不在遥远的过去（避免调度一个立即过期的任务）
        # 如果 schedule_time 已经过去，那么这个信号可能是处理一个非常旧的或者被修改过的预订
        # 可以选择立即调度，或者让每日任务捕获，这里选择立即调度（async在过去时间会立即执行）

        # 调度 Celery 任务
        # 使用 apply_async 的 eta 参数来指定任务的执行时间
        create_no_show_violation_for_single_booking.apply_async(
            args=[instance.pk],
            eta=schedule_time,
            # 可以通过 task_id 指定唯一ID，用于将来撤销任务。
            # 但考虑到任务内部的幂等性检查，即使重复执行也不会导致重复违规，所以此处暂时省略 task_id
            # task_id=f"no_show_check_booking_{instance.pk}",
        )
        logger.info(f"为预订 {instance.pk} 调度了未到场检查任务，计划在 {schedule_time} 执行。")

    # 后续处理逻辑：如果预订状态改变 (例如被取消、被签到)
    # 1. 如果 end_time 改变，且仍是 APPROVED，则新的 task 会被调度，旧的 task 运行时会因为 status 或 end_time 不匹配而提前退出。
    # 2. 如果状态变为非 APPROVED (CANCELED, CHECKED_IN 等)，且之前有任务被调度，则旧的 task 运行时也会因为状态不匹配而提前退出。
    # 因此，我们无需在这里显式撤销旧任务，只需依赖任务本身的健壮性即可，这简化了逻辑。