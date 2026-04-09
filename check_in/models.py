# check_in/models.py

from django.db import models
from django.db.models import Index, Manager
from django.core.exceptions import ValidationError
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

# 导入外部模型
# 注意：这里只需要导入 CustomUser 和 spaces 相关的常量
# Booking 模型现在通过字符串引用，以避免循环导入
try:
    from users.models import CustomUser
    from spaces.models import (
        CHECK_IN_METHOD_CHOICES,
        CHECK_IN_METHOD_NONE,
        CHECK_IN_METHOD_SELF,
        CHECK_IN_METHOD_STAFF,
        CHECK_IN_METHOD_HYBRID,
        CHECK_IN_METHOD_LOCATION
    )
except ImportError as e:
    logger.error(f"Failed to import necessary models in check_in/models.py: {e}. CheckInRecord model might not function correctly.")
    CHECK_IN_METHOD_CHOICES = []

class CheckInRecord(models.Model):
    """
    记录预订的签到事件。
    一个预订可以对应多条签到记录（例如，如果支持多次签到/签出）。
    """
    objects: Manager = models.Manager()

    booking = models.ForeignKey( # <--- 修正：改为 ForeignKey
        'bookings.Booking', # <--- 修正：使用字符串引用，解决循环导入
        on_delete=models.CASCADE,
        related_name='check_in_records', # <--- 修正：改为 'check_in_records' (复数)
        verbose_name="关联预订",
        help_text="此签到记录关联的预订"
    )
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='check_in_as_user',
        verbose_name="签到用户",
        help_text="本次签到记录的主体用户，通常是预订发起人"
    )
    checked_in_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='performed_check_ins',
        verbose_name="签到执行人",
        help_text="执行签到操作的人（可以是用户本人，也可以是工作人员）"
    )
    check_in_time = models.DateTimeField(
        verbose_name="签到时间",
        help_text="实际签到发生的时间"
    )
    check_in_image = models.ImageField(
        upload_to='check_in_images/',
        blank=True,
        null=True,
        verbose_name="签到图片",
        help_text="用户或签到员在签到时上传的图片（例如：环境照片、自拍照）"
    )
    latitude = models.DecimalField(
        max_digits=12,
        decimal_places=10,
        null=True,
        blank=True,
        verbose_name="签到纬度",
        help_text="签到时的地理纬度"
    )
    longitude = models.DecimalField(
        max_digits=13,
        decimal_places=10,
        null=True,
        blank=True,
        verbose_name="签到经度",
        help_text="签到时的地理经度"
    )
    check_in_method = models.CharField(
        max_length=10,
        choices=CHECK_IN_METHOD_CHOICES,
        default=CHECK_IN_METHOD_HYBRID,
        verbose_name="签到方式",
        help_text="实际执行签到时使用的方法（如：SELF, STAFF, HYBRID, LOCATION）。注意：此字段记录实际操作，而非空间配置。"
    )
    is_valid = models.BooleanField(
        default=True,
        verbose_name="是否有效",
        help_text="此签到记录是否被认为是有效签到（例如：在预订时间内由授权用户完成）。用于异常情况标记。"
    )
    notes = models.TextField(
        blank=True,
        verbose_name="备注",
        help_text="针对本次签到的额外说明"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = '签到记录'
        verbose_name_plural = verbose_name
        ordering = ['-check_in_time']
        permissions = [
            ("can_view_checkinrecord", "Can view check-in records"),
            ("can_add_checkinrecord", "Can add check-in records"),
            ("can_change_checkinrecord", "Can change check-in records"),
            ("can_delete_checkinrecord", "Can delete check-in records"),
        ]
        indexes = [
            Index(fields=['booking']),
            Index(fields=['user']),
            Index(fields=['checked_in_by']),
            Index(fields=['check_in_time']),
            Index(fields=['is_valid']),
            Index(fields=['latitude', 'longitude']),
        ]

    def __str__(self):
        booking_target = "未知目标"
        # 在这里，由于 booking 字段是字符串引用，直接访问 self.booking.related_space 可能仍然导致循环导入问题
        # 如果需要获取相关信息，可能需要使用 select_related 或在需要时按需获取
        # 或者，如果 booking 已经被 select_related 预加载，则可以直接访问
        if hasattr(self, 'booking') and self.booking and hasattr(self.booking, 'related_space') and self.booking.related_space:
             booking_target = self.booking.related_space.name
        check_in_time_str = self.check_in_time.strftime('%Y-%m-%d %H:%M')
        return f"用户 {self.user.get_full_name} 在 {booking_target} 签到于 {check_in_time_str}"

    def clean(self):
        super().clean()
        # 确保 user 和 checked_in_by 字段被正确赋值
        if self.booking and not self.user:
            self.user = self.booking.user # 默认签到用户为预订用户
        if not self.checked_in_by:
            self.checked_in_by = self.user # 默认签到执行人为签到用户

        # 如果预订已存在，确保签到记录的用户与预订用户一致
        if self.booking and self.user and self.booking.user != self.user:
            raise ValidationError({'user': '签到记录的用户必须与关联预订的用户一致。'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def _get_related_object_dict(self, obj, include_related=False):
        # 辅助方法，用于将关联对象转换为字典，避免无限循环
        if obj is None:
            return None
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict(include_related=include_related)
        elif hasattr(obj, 'pk') and hasattr(obj, '__str__'):
            # 对于 CustomUser，返回其 ID 和名称
            if isinstance(obj, CustomUser):
                return {'id': obj.id, 'username': obj.username, 'name': obj.get_full_name()}
            return {'id': obj.pk, 'name': str(obj)}
        return None

    def to_dict(self, include_related: bool = True) -> dict:
        data = {
            'id': self.id,
            'booking_id': self.booking.id, # 仅包含 ID，避免循环引用
            'check_in_time': self.check_in_time.isoformat(),
            'check_in_method': self.check_in_method,
            'is_valid': self.is_valid,
            'notes': self.notes,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'latitude': str(self.latitude) if self.latitude is not None else None,
            'longitude': str(self.longitude) if self.longitude is not None else None,
        }
        if self.check_in_image:
            data['check_in_image_url'] = self.check_in_image.url
        if include_related:
            # 不再包含 booking 的完整 to_dict，避免深度循环
            data['user'] = self._get_related_object_dict(self.user, include_related=False)
            data['checked_in_by'] = self._get_related_object_dict(self.checked_in_by, include_related=False)
        return data