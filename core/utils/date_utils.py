# core/utils/date_utils.py
from datetime import datetime, timedelta, time
from django.core.exceptions import ValidationError
from django.utils import timezone


def validate_booking_time_integrity(start_time: datetime, end_time: datetime):
    """
    校验预订时间的完整性，包括开始/结束顺序和是否在未来。
    """
    if not start_time or not end_time:
        raise ValidationError('预订的开始时间和结束时间不能为空。')

    if start_time >= end_time:
        raise ValidationError({'end_time': '结束时间必须晚于开始时间。'})

    # 预订开始时间不能早于当前时间
    # 注意：这里假设只对新创建的预订做严格检查，修改现有预订的逻辑可能不同
    # 由于该函数会在 Booking.clean() 中被调用，对于 instance.pk 存在的旧对象
    # 如果允许修改过去时间段的预订（例如取消过去的NO_SHOW），此处可能需要调整
    # 但对于默认用户前端创建的预订，这是一项重要的检查
    current_time = timezone.now()
    if start_time < current_time:
        raise ValidationError({'start_time': '不能预订过去的时间。'})

    # 暂时不允许跨天预订，因为 SpaceType 的 उपलब्ध时间字段用的是 TimeField
    if start_time.date() != end_time.date():
        raise ValidationError('预订目前不支持跨越午夜，请确保开始和结束在同一天。')


def validate_booking_duration(start_time: datetime, end_time: datetime,
                              min_duration: timedelta | None, max_duration: timedelta | None):
    """
    校验预订时长是否符合最小/最大限制。
    """
    booking_duration = end_time - start_time

    if min_duration and booking_duration < min_duration:
        min_duration_minutes = int(min_duration.total_seconds() / 60)
        raise ValidationError(f"预订时长不能少于 {min_duration_minutes} 分钟。")

    if max_duration and booking_duration > max_duration:
        max_duration_hours = int(max_duration.total_seconds() / 3600)
        if max_duration_hours == 0 and max_duration.total_seconds() > 0:  # 如果 max_duration 小于1小时，按分钟显示
            max_duration_minutes = int(max_duration.total_seconds() / 60)
            raise ValidationError(f"预订时长不能超过 {max_duration_minutes} 分钟。")
        elif max_duration_hours > 0:
            raise ValidationError(f"预订时长不能超过 {max_duration_hours} 小时。")


def validate_booking_daily_availability(start_time: datetime, end_time: datetime,
                                        available_start_time: time | None, available_end_time: time | None):
    """
    校验预订时间是否落在空间的每日可用时间范围内。
    """
    # 如果没有定义可用时间，则认为全天可用（不进行此项检查）
    if not available_start_time and not available_end_time:
        return

    booking_start_time_only = start_time.time()
    booking_end_time_only = end_time.time()

    if available_start_time and booking_start_time_only < available_start_time:
        raise ValidationError(f"预订不能早于每日最早可预订时间 {available_start_time.strftime('%H:%M')}。")

    # 这里的逻辑是预订必须在 available_end_time 之前完成。
    # 例如，如果 available_end_time 是 22:00，则预订结束时间不能是 22:01。
    if available_end_time and booking_end_time_only > available_end_time:
        raise ValidationError(f"预订不能晚于每日最晚可预订时间 {available_end_time.strftime('%H:%M')}。")