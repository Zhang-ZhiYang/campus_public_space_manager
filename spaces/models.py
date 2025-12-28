# spaces/models.py
from django.db import models
from django.db.models import Manager
from datetime import timedelta
from django.core.exceptions import ValidationError  # 导入 ValidationError 用于模型自定义验证


class Amenity(models.Model):
    """
    空间设施模型，例如投影仪、白板、Wi-Fi等。
    """
    objects: Manager = Manager()

    name = models.CharField(max_length=100, unique=True, verbose_name="设施名称")
    description = models.TextField(blank=True, verbose_name="设施描述")

    class Meta:
        verbose_name = '设施'
        verbose_name_plural = verbose_name
        ordering = ['name']

    def __str__(self):
        return self.name


class Space(models.Model):
    """
    可预订空间模型，定义了每个空间的属性和预订规则。
    """
    objects: Manager = Manager()

    name = models.CharField(max_length=255, unique=True, verbose_name="空间名称")
    location = models.CharField(max_length=255, verbose_name="位置信息", help_text="例如：B座301室")
    description = models.TextField(blank=True, verbose_name="详细描述", help_text="空间的详细介绍和使用注意事项")
    capacity = models.PositiveIntegerField(default=1, verbose_name="容量", help_text="可容纳人数")
    is_bookable = models.BooleanField(default=True, verbose_name="是否可预订", help_text="空间是否对外开放预订")
    is_active = models.BooleanField(default=True, verbose_name="是否启用", help_text="空间是否处于启用状态")
    image = models.ImageField(upload_to='space_images/', blank=True, null=True, verbose_name="空间图片")
    amenities = models.ManyToManyField(Amenity, blank=True, related_name='spaces', verbose_name="空间设施")

    requires_approval = models.BooleanField(
        default=True,  # 默认需要审批
        verbose_name="需要管理员审批",
        help_text="预订此空间是否需要管理员审核批准"
    )

    available_start_time = models.TimeField(null=True, blank=True, verbose_name="每日最早可预订时间",
                                            help_text="例如 08:00")
    available_end_time = models.TimeField(null=True, blank=True, verbose_name="每日最晚可预订时间",
                                          help_text="例如 22:00")

    min_booking_duration = models.DurationField(default=timedelta(minutes=30), verbose_name="单次预订最短时长",
                                                help_text="例如 30 分钟")
    max_booking_duration = models.DurationField(default=timedelta(hours=4), verbose_name="单次预订最长时长",
                                                help_text="例如 4 小时")
    buffer_time_minutes = models.PositiveIntegerField(default=0, verbose_name="前后预订缓冲时间(分钟)",
                                                      help_text="相邻预订之间的最短间隔（分钟）")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = '空间'
        verbose_name_plural = verbose_name
        ordering = ['name']

    def clean(self):
        """
        模型层面的业务逻辑验证：不活跃空间不能设置为可预订。
        """
        super().clean()
        if not self.is_active and self.is_bookable:
            raise ValidationError({'is_bookable': '不活跃的空间不能设置为可预订。'})

        # 也可以在这里添加时间段的逻辑验证，但通常在序列化器中更合适
        if self.available_start_time and self.available_end_time and \
                self.available_start_time >= self.available_end_time:
            raise ValidationError({'available_end_time': '每日最晚可预订时间必须晚于最早可预订时间。'})

    def save(self, *args, **kwargs):
        """
        重写 save 方法，确保在保存时不活跃的空间自动设置为不可预订。
        调用 full_clean() 来触发 clean() 方法进行模型验证。
        """
        if not self.is_active:
            self.is_bookable = False  # 如果空间不活跃，强制设置为不可预订

        # 在保存前执行完整的模型验证
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.location})"