# check_in/dao/check_in_dao.py
from typing import Optional, List, Dict, Any

from django.db import models
from django.db.models import QuerySet
from core.dao import BaseDAO
from check_in.models import CheckInRecord
from bookings.models import Booking  # 用于类型提示和关联查询
from users.models import CustomUser  # 用于类型提示和关联查询
from spaces.models import Space  # 用于类型提示和关联查询
import datetime  # 导入 datetime 模块用于类型提示


class CheckInRecordDAO(BaseDAO):
    """
    CheckInRecord 数据的访问对象。
    提供了签到记录的 CRUD 操作和查询。
    """
    model = CheckInRecord

    def get_base_queryset(self) -> QuerySet[CheckInRecord]:
        """
        获取一个带有常用预加载的基础 CheckInRecord QuerySet。
        预加载 associated `booking`, `user` 和 `checked_in_by`.
        同时深度预加载 `booking.related_space` 和 `booking.related_space.space_type`。
        """
        return self.model.objects.select_related(
            'booking',
            'user',
            'checked_in_by',
            'booking__related_space__space_type'  # 深度预加载 related_space 及其 space_type
        )

    def _apply_eager_loading(self, queryset: QuerySet[CheckInRecord], prefetch_related: list = None,
                             select_related: list = None) -> QuerySet[CheckInRecord]:
        """内部辅助方法，用于在基础QuerySet之上应用动态的预加载优化。"""
        if select_related:
            queryset = queryset.select_related(*select_related)
        if prefetch_related:
            queryset = queryset.prefetch_related(*prefetch_related)
        return queryset

    def get_all_records(self, prefetch_related: list = None, select_related: list = None) -> QuerySet[CheckInRecord]:
        """
        获取所有 CheckInRecord 对象的 QuerySet。
        """
        queryset = self.get_base_queryset().order_by('-check_in_time')
        return self._apply_eager_loading(queryset, prefetch_related, select_related)

    def get_record_by_id(self, pk: int, prefetch_related: list = None, select_related: list = None) -> Optional[
        CheckInRecord]:
        """
        根据 ID 获取单个 CheckInRecord 对象。
        """
        queryset = self.get_base_queryset().filter(pk=pk)
        return self._apply_eager_loading(queryset, prefetch_related, select_related).first()

    def get_record_by_booking_id(self, booking_id: int, prefetch_related: list = None, select_related: list = None) -> \
    Optional[CheckInRecord]:
        """
        根据关联 Booking ID 获取单个 CheckInRecord 对象。
        因为 `booking` 字段是 OneToOneField, 所以结果将是唯一的。
        """
        queryset = self.get_base_queryset().filter(booking_id=booking_id)
        return self._apply_eager_loading(queryset, prefetch_related, select_related).first()

    def get_records_by_user(self, user: CustomUser, prefetch_related: list = None, select_related: list = None) -> \
    QuerySet[CheckInRecord]:
        """
        获取某个用户作为**签到主体**的所有 CheckInRecord。
        """
        queryset = self.get_base_queryset().filter(user=user).order_by('-check_in_time')
        return self._apply_eager_loading(queryset, prefetch_related, select_related)

    def get_records_performed_by_user(self, checked_in_by_user: CustomUser, prefetch_related: list = None,
                                      select_related: list = None) -> QuerySet[CheckInRecord]:
        """
        获取某个用户作为**签到执行人**的所有 CheckInRecord。
        """
        queryset = self.get_base_queryset().filter(checked_in_by=checked_in_by_user).order_by('-check_in_time')
        return self._apply_eager_loading(queryset, prefetch_related, select_related)

    def get_records_for_space(self, space_pk: int, prefetch_related: list = None, select_related: list = None) -> \
    QuerySet[CheckInRecord]:
        """
        获取与特定空间（包括其设施）相关的所有 CheckInRecord。
        """
        queryset = self.get_base_queryset().filter(
            models.Q(booking__space__pk=space_pk) | models.Q(booking__bookable_amenity__space__pk=space_pk)
        ).order_by('-check_in_time')
        return self._apply_eager_loading(queryset, prefetch_related, select_related)

    # CRUD 操作封装
    def create_record(self,
                      booking: Booking,
                      user: CustomUser,
                      checked_in_by: CustomUser,
                      check_in_time: datetime.datetime,  # 使用 datetime.datetime 进行精确类型提示
                      check_in_method: str,
                      is_valid: bool = True,
                      notes: str = ""
                      ) -> CheckInRecord:
        """
        创建一条新的签到记录。
        """
        return self.create(
            booking=booking,
            user=user,
            checked_in_by=checked_in_by,
            check_in_time=check_in_time,
            check_in_method=check_in_method,
            is_valid=is_valid,
            notes=notes
        )

    def update_record(self,
                      record: CheckInRecord,
                      is_valid: Optional[bool] = None,
                      notes: Optional[str] = None
                      ) -> CheckInRecord:
        """
        更新一条现有签到记录，主要用于更改有效性或备注。
        """
        update_fields = {}
        if is_valid is not None:
            update_fields['is_valid'] = is_valid
        if notes is not None:
            update_fields['notes'] = notes

        return self.update(record, **update_fields)

    def delete_record(self, record: CheckInRecord):
        """
        删除一条签到记录。
        """
        self.delete(record)