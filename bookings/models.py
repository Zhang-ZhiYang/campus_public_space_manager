# bookings/models.py
from django.db import models
from django.db.models import Manager
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

# 从其他应用导入相关模型 (假设 users.models.CustomUser 存在且具有 total_violation_count 字段)
# 注意: 因为 Role 和 CustomUser 不变，这里直接引用 CustomUser。
# 如果 CustomUser 真的没有 total_violation_count 字段，这里会报错，需要你自行添加。
try:
    from users.models import CustomUser
except ImportError:
    # 临时代替 CustomUser，用于让模型定义通过
    class CustomUser(models.Model):
        username = models.CharField(max_length=150, unique=True)
        total_violation_count = models.PositiveIntegerField(default=0)

        # 模拟 save 方法，防止信号处理时报错
        def save(self, *args, **kwargs):
            pass


    print("Warning: users.models.CustomUser could not be imported. Using a mock CustomUser for model definition. "
          "Ensure 'users' app is in INSTALLED_APPS and CustomUser has 'total_violation_count' field.")

from spaces.models import Space, BookableAmenity

# ====================================================================
# Booking 状态选择项
# ====================================================================
BOOKING_STATUS_CHOICES = (
    ('PENDING', '待审核'),
    ('APPROVED', '已批准'),
    ('REJECTED', '已拒绝'),
    ('CANCELLED', '已取消'),
    ('COMPLETED', '已完成'),
    ('NO_SHOW', '未到场'),
)

# ====================================================================
# Violation 类型选择项
# ====================================================================
VIOLATION_TYPE_CHOICES = (
    ('NO_SHOW', '未到场'),
    ('LATE_CANCELLATION', '迟取消'),
    ('MISUSE_SPACE', '违规使用'),
    ('DAMAGE_PROPERTY', '设施损坏'),
    ('EXCEED_CAPACITY', '超员使用'),
    ('OCCUPY_OVERTIME', '超时占用'),
    ('OTHER', '其他'),
)


# ====================================================================
# Booking Model (预订)
# ====================================================================
class Booking(models.Model):
    """
    用户预订特定空间或空间内特定可预订设施的模型。
    增强：新增 booked_quantity 字段，强化 clean 方法的预订目标校验。
    """
    objects: Manager = Manager()

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='bookings',
        verbose_name="预订用户",
        help_text="发起预订请求的用户"
    )

    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='space_bookings',
        verbose_name="预订空间",
        help_text="被用户预订的空间（如果预订的是整个空间）"
    )
    bookable_amenity = models.ForeignKey(
        BookableAmenity,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='amenity_bookings',
        verbose_name="预订设施实例",
        help_text="被用户预订的空间内设施实例（如果预订的是特定设施）"
    )

    booked_quantity = models.PositiveIntegerField(
        default=1,
        verbose_name="预订数量",
        help_text="预订的数量。如果预订的是设施，指设施数量；预订空间，此字段应为1。"
    )

    start_time = models.DateTimeField(verbose_name="开始时间")
    end_time = models.DateTimeField(verbose_name="结束时间")
    purpose = models.TextField(
        blank=True,
        verbose_name="预订用途",
        help_text="用户预订此目标的具体目的或活动"
    )
    status = models.CharField(
        max_length=20,
        choices=BOOKING_STATUS_CHOICES,
        default='PENDING',
        verbose_name="预订状态",
        help_text="预订的当前状态"
    )
    admin_notes = models.TextField(
        blank=True,
        verbose_name="管理员备注",
        help_text="管理员对本次预订的内部记录或说明"
    )
    reviewed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_bookings',
        verbose_name="审核人员",
        help_text="审核本条预订请求的管理员/空间管理员"
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="审核时间"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = '预订'
        verbose_name_plural = verbose_name
        ordering = ['-start_time']
        # 添加索引
        indexes = [
            # 用于查询特定用户的所有预订
            models.Index(fields=['user', 'start_time', 'end_time']),
            # 用于查询特定空间在某个时间范围内的预订 (可用性检查的核心)
            models.Index(fields=['space', 'start_time', 'end_time']),
            # 用于查询特定设施实例在某个时间范围内的预订 (可用性检查的核心)
            models.Index(fields=['bookable_amenity', 'start_time', 'end_time']),
            # 针对状态过滤的索引 (如果 status 经常用作过滤条件)
            models.Index(fields=['status']),
            # 用于快速查询所有未来预订或其他时间范围内的预订
            models.Index(fields=['start_time']),
            models.Index(fields=['end_time']),
        ]

    def __str__(self):
        target_name = "未知目标"
        if self.space:
            target_name = self.space.name
        elif self.bookable_amenity:
            target_name = f"{self.bookable_amenity.space.name} 的 {self.bookable_amenity.amenity.name}"

        return (f"{self.user.username} 预订 {target_name} ({self.booked_quantity}个) "
                f"从 {self.start_time.strftime('%Y-%m-%d %H:%M')} 到 {self.end_time.strftime('%H:%M')} "
                f"[{self.get_status_display()}]")

    def clean(self):
        """
        在保存前执行自定义验证。
        - 确保开始时间早于结束时间。
        - 确保只能预订 Space 或 BookableAmenity 中的一个。
        - 校验 booked_quantity
        """
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({'end_time': '结束时间必须晚于开始时间。'})

        # 确保只预订一个目标：使用异或 (XOR) 逻辑
        if not ((self.space is not None) ^ (self.bookable_amenity is not None)):
            raise ValidationError('预订必须且只能指定一个目标：空间或设施实例。')

        # 如果预订的是设施实例，校验 booked_quantity
        if self.bookable_amenity:
            if self.booked_quantity <= 0:
                raise ValidationError({'booked_quantity': '预订设施时，预订数量必须大于0。'})
            if self.booked_quantity > self.bookable_amenity.quantity:
                raise ValidationError(
                    {'booked_quantity': f"预订数量不能超过设施总数量 {self.bookable_amenity.quantity}。"})
            # 此外，设施实例必须是活跃且可预订的 (这个业务逻辑最好放在 Service 层，但作为模型层基础校验也可)
            if not self.bookable_amenity.is_active or not self.bookable_amenity.is_bookable:
                raise ValidationError("所选设施不可预订或未启用。")

        # 如果预订的是整个空间，booked_quantity 应该为 1
        if self.space:
            if self.booked_quantity != 1:
                raise ValidationError({'booked_quantity': '预订整个空间时，数量必须为1。'})
            # 此外，空间本身必须是活跃且可预订的 (这个业务逻辑最好放在 Service 层，但作为模型层基础校验也可)
            if not self.space.is_active or not self.space.is_bookable:
                raise ValidationError("所选空间不可预订或未启用。")

    def save(self, *args, **kwargs):
        self.full_clean()  # 确保在保存前调用 clean 方法
        super().save(*args, **kwargs)


# ====================================================================
# Violation Model (违约记录)
# ====================================================================
class Violation(models.Model):
    objects: Manager = Manager()

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='violations',
        verbose_name="违约用户",
        help_text="发生违约行为的用户"
    )
    booking = models.ForeignKey(
        Booking,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='violation_records',
        verbose_name="关联预订",
        help_text="与本次违约行为相关的预订（可选）"
    )
    violation_type = models.CharField(
        max_length=50,
        choices=VIOLATION_TYPE_CHOICES,
        verbose_name="违约类型"
    )
    description = models.TextField(
        verbose_name="违约详情",
        help_text="对违约行为的具体描述"
    )
    penalty_points = models.PositiveIntegerField(
        default=1,
        verbose_name="扣除积分/增加违约次数",
        help_text="本次违约行为导致用户扣除的积分或增加的违约次数"
    )
    issued_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issued_violations',
        verbose_name="记录人员",
        help_text="记录本次违约行为的管理员/空间管理员"
    )
    issued_at = models.DateTimeField(auto_now_add=True, verbose_name="记录时间")
    is_resolved = models.BooleanField(
        default=False,
        verbose_name="是否已解决",
        help_text="本次违约是否已经处理或解决"
    )

    class Meta:
        verbose_name = '违约记录'
        verbose_name_plural = verbose_name
        ordering = ['-issued_at']
        # 添加索引
        indexes = [
            models.Index(fields=['user']),  # 常用查询字段
            models.Index(fields=['booking']),  # 常用查询字段
            models.Index(fields=['violation_type']),  # 常用过滤字段
            models.Index(fields=['issued_at']),  # 常用排序和日期范围
        ]

    def __str__(self):
        return (f"违约记录 ({self.get_violation_type_display()}) - "
                f"用户: {self.user.username} - "
                f"时间: {self.issued_at.strftime('%Y-%m-%d %H:%M')}")


# ====================================================================
# Django Signals for Violation model (关联 CustomUser 的 total_violation_count)
# ====================================================================
@receiver(post_save, sender=Violation)
def update_user_violation_count_on_save(sender, instance, created, **kwargs):
    """
    在 Violation 实例创建或更新后，更新用户总违约次数。
    """
    if created:  # 仅在新创建违约记录时增加总违约次数
        instance.user.total_violation_count += instance.penalty_points
        instance.user.save(update_fields=['total_violation_count'])
    # 对于更新场景，如果 penalty_points 变化，需要更复杂的逻辑来调整总数。
    # 为避免复杂性，假定 penalty_points 一旦记录不轻易修改。


@receiver(post_delete, sender=Violation)
def update_user_violation_count_on_delete(sender, instance, **kwargs):
    """
    在 Violation 实例被删除后，减少用户总违约次数。
    """
    instance.user.total_violation_count = max(0, instance.user.total_violation_count - instance.penalty_points)
    instance.user.save(update_fields=['total_violation_count'])