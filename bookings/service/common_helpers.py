# bookings/service/common_helpers.py (仅显示修正部分)
from datetime import datetime, timedelta, time  # 导入 time, timedelta
from typing import List, Dict, Any, Union, Tuple, Optional
from django.utils import timezone


class CommonBookingHelpers:
    """
    提供不依赖特定DAO/Service实例的通用预订辅助函数。
    这些函数通常只处理纯数据结构和简单逻辑，不涉及数据库交互。
    """

    @staticmethod
    def is_time_slot_available(booked_slots: List[Dict[str, Union[datetime, int]]],
                               new_start: datetime, new_end: datetime,
                               booked_quantity: int,
                               total_capacity: int,
                               buffer_time_minutes: int = 0) -> bool:
        """
        **优化后：采用扫描线算法**。纯逻辑函数，检查给定新时间段内的并发预订是否会超过总容量。
        此函数不进行数据库查询，而是基于传入的“已预订槽位”数据进行计算。

        :param booked_slots: 列表中每个元素是字典，包含 'start_time', 'end_time', 'booked_quantity'。
                             这些是与新预订目标（空间或设施）相同的所有已批准/待批准预订。
        :param new_start: 新预订的开始时间。
        :param new_end: 新预订的结束时间。
        :param booked_quantity: 新预订的需求数量。
        :param total_capacity: 目标（空间或设施）的总容量/总数量。
        :param buffer_time_minutes: 每个预订前后的缓冲时间（分钟）。
        :return: 如果时间槽可用且未超容量，返回 True；否则返回 False。
        """

        # Step 1: 收集所有事件点 (时间, 数量变化)
        events: List[Tuple[datetime, int]] = []

        # 考虑到缓冲时间，扩展新预订的时间范围
        effective_new_start = new_start - timedelta(minutes=buffer_time_minutes)
        effective_new_end = new_end + timedelta(minutes=buffer_time_minutes)

        # 添加现有已预订槽位的事件
        for slot in booked_slots:
            slot_start = slot['start_time']
            slot_end = slot['end_time']
            slot_quantity = slot['booked_quantity']

            effective_slot_start = slot_start - timedelta(minutes=buffer_time_minutes)
            effective_slot_end = slot_end + timedelta(minutes=buffer_time_minutes)

            # 仅考虑与新预订有效时间段有实际重叠的现有预订
            if effective_new_end > effective_slot_start and effective_new_start < effective_slot_end:
                events.append((effective_slot_start, slot_quantity))
                events.append((effective_slot_end, -slot_quantity))

        # 添加新预订的事件
        events.append((effective_new_start, booked_quantity))
        events.append((effective_new_end, -booked_quantity))

        # Step 2: 排序事件点
        # 优先按时间排序。如果时间相同，优先处理结束事件（-数量），再处理开始事件（+数量）。
        # 这可以避免在一个预订刚好结束同时另一个开始时产生瞬间的容量超载假象。
        events.sort(key=lambda x: (x[0], 1 if x[1] < 0 else 0))  # x[1] < 0 表示结束事件，让它先处理

        # Step 3: 扫描事件点，计算当前占用和最大占用
        current_occupancy = 0
        max_occupancy_observed = 0

        for event_time, quantity_change in events:
            current_occupancy += quantity_change
            if current_occupancy > total_capacity:
                return False  # 即刻判定不可用

            max_occupancy_observed = max(max_occupancy_observed, current_occupancy)

        return max_occupancy_observed <= total_capacity

    @staticmethod
    def _get_datetime_from_time(date_obj: datetime.date, time_obj: time) -> datetime:  # <--- 修正这里
        """
        结合一个 `date` 对象和 `time` 对象，创建一个 `datetime` 对象，并使其 timezone-aware。
        """
        return timezone.make_aware(datetime.combine(date_obj, time_obj))

    @staticmethod
    def get_time_boundaries_for_day(date_obj: datetime.date,  # <--- 修正这里
                                    available_start_time: Optional[time],
                                    available_end_time: Optional[time]) -> Dict[str, datetime]:
        """
        根据日期和可选的每日可用时间，计算当日的有效开始和结束 datetime。
        如果 available_start_time/end_time 为 None，则默认为当天 00:00:00 和 23:59:59。
        """
        effective_start_t = available_start_time if available_start_time else time(0, 0, 0)
        effective_end_t = available_end_time if available_end_time else time(23, 59, 59)

        start_dt = CommonBookingHelpers._get_datetime_from_time(date_obj, effective_start_t)
        end_dt = CommonBookingHelpers._get_datetime_from_time(date_obj, effective_end_t)

        return {'start_datetime': start_dt, 'end_datetime': end_dt}

    @staticmethod
    def format_duration(duration: timedelta) -> str:
        """
        将 timedelta 对象格式化为更友好的可读字符串（例如“1小时30分钟”）。
        """
        total_seconds = int(duration.total_seconds())
        if total_seconds < 0:
            return "无效时长"

        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        parts = []
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分钟")
        if seconds > 0 and not parts:
            parts.append(f"{seconds}秒")

        if not parts:
            return "0分钟"

        return "".join(parts)