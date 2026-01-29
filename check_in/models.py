# check_in/models.py
from django.db import models
from django.db.models import Index, Manager
from django.core.exceptions import ValidationError
from django.utils import timezone

from users.models import CustomUser
from bookings.models import Booking
from spaces.models import ( # 从 spaces.models 导入签到方式常量和 CHOICES
    Space,
    CHECK_IN_METHOD_CHOICES,
    CHECK_IN_METHOD_NONE,
    CHECK_IN_METHOD_SELF,
    CHECK_IN_METHOD_STAFF,
    CHECK_IN_METHOD_HYBRID,
    CHECK_IN_METHOD_LOCATION # <--- 新增
)

class CheckInRecord(models.Model):
    """
    记录预订的签到事件。
    一个预订通常对应一个签到记录（对于主要预订人）。
    """
    objects: Manager = models.Manager()

    booking = models.OneToOneField( # 使用 OneToOneField 确保一个 Booking 只有一条 CheckInRecord
        Booking,
        on_delete=models.CASCADE,
        related_name='check_in_record',
        verbose_name="关联预订",
        help_text="此签到记录关联的唯一预订"
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
    # NEW: 添加签到时的地理坐标，以便记录和验证
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name="签到纬度",
        help_text="签到时的地理纬度"
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name="签到经度",
        help_text="签到时的地理经度"
    )
    check_in_method = models.CharField(
        max_length=10,
        choices=CHECK_IN_METHOD_CHOICES,
        default=CHECK_IN_METHOD_HYBRID, # 记录实际使用的签到方式，可表示“用户自行”、“工作人员代签”等
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
        permissions = (
            ("can_view_checkin_records", "Can view any check-in record"),
            ("can_check_in_any_booking", "Can perform check-in for any booking"), # 用于工作人员全局签到
            ("can_manage_checkin_records", "Can create, edit, delete check-in records"), # 用于管理签到记录本身 (编辑通常指更改 is_valid 或 notes)
        )
        indexes = [
            Index(fields=['booking']),
            Index(fields=['user']),
            Index(fields=['checked_in_by']),
            Index(fields=['check_in_time']),
            Index(fields=['is_valid']),
            Index(fields=['latitude', 'longitude']), # <--- 新增索引
        ]

    def __str__(self):
        booking_target = "未知目标"
        if self.booking and self.booking.related_space:
             booking_target = self.booking.related_space.name
        check_in_time_str = self.check_in_time.strftime('%Y-%m-%d %H:%M')
        return f"用户 {self.user.get_full_name} 在 {booking_target} 签到于 {check_in_time_str}"

    def clean(self):
        super().clean()
        if self.booking and self.booking.user != self.user:
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
            return {'id': obj.pk, 'name': str(obj)}
        return None

    def to_dict(self, include_related: bool = True) -> dict:
        data = {
            'id': self.id,
            'check_in_time': self.check_in_time.isoformat(),
            'check_in_method': self.check_in_method,
            'is_valid': self.is_valid,
            'notes': self.notes,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'latitude': float(self.latitude) if self.latitude is not None else None, # <--- 包含新字段
            'longitude': float(self.longitude) if self.longitude is not None else None, # <--- 包含新字段
        }
        if self.check_in_image:
            data['check_in_image_url'] = self.check_in_image.url
        if include_related:
            data['booking'] = self._get_related_object_dict(self.booking, include_related=False)
            data['user'] = self._get_related_object_dict(self.user, include_related=False)
            data['checked_in_by'] = self._get_related_object_dict(self.checked_in_by, include_related=False)
        return data