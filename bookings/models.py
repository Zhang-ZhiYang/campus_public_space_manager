# bookings/models.py
from django.db import models
from django.db.models import Manager
from datetime import timedelta

# 从其他应用导入相关模型
from users.models import CustomUser  # 假设 CustomUser 在 users.models 中
from spaces.models import Space  # 假设 Space 在 spaces.models 中

# ====================================================================
# Booking 状态选择项
# ====================================================================
BOOKING_STATUS_CHOICES = (
    ('PENDING', '待审核'),  # 需要管理员审批的空间
    ('APPROVED', '已批准'),  # 预订已确认，空间已占用
    ('REJECTED', '已拒绝'),  # 预订被管理员拒绝
    ('CANCELLED', '已取消'),  # 用户或管理员取消预订
    ('COMPLETED', '已完成'),  # 预订时间已过，且用户正常使用
    ('NO_SHOW', '未到场'),  # 预订时间已过，用户未到场，可能触发违约
)

# ====================================================================
# Violation 类型选择项
# ====================================================================
VIOLATION_TYPE_CHOICES = (
    ('NO_SHOW', '未到场'),
    ('LATE_CANCELLATION', '迟取消'),
    ('MISUSE_SPACE', '违规使用'),  # 例如，在禁烟区吸烟
    ('DAMAGE_PROPERTY', '设施损坏'),  # 损坏空间内部物品
    ('EXCEED_CAPACITY', '超员使用'),  # 预订时人数与实际使用人数不符，且超出容量
    ('OCCUPY_OVERTIME', '超时占用'),  # 超出预订结束时间仍占用
    ('OTHER', '其他'),
)


# ====================================================================
# Booking Model (预订)
# ====================================================================
class Booking(models.Model):
    """
    用户预订特定空间的模型。
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
        related_name='bookings',
        verbose_name="预订空间",
        help_text="被用户预订的空间"
    )
    start_time = models.DateTimeField(verbose_name="开始时间")
    end_time = models.DateTimeField(verbose_name="结束时间")
    purpose = models.TextField(
        blank=True,
        verbose_name="预订用途",
        help_text="用户预订此空间的具体目的或活动"
    )
    status = models.CharField(
        max_length=20,
        choices=BOOKING_STATUS_CHOICES,
        default='PENDING',  # 默认待审核，可由空间requires_approval字段决定是否直接APPROVED
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
        # 确保同一空间、同一时间段内没有重复的APPROVED预订
        # 注意：此处唯一性约束需在 clean() 方法或序列化器中进行更复杂的逻辑校验
        # DB层面的 unique_together 无法处理时间段重叠问题
        # unique_together = ('space', 'start_time', 'end_time') # 暂时注释，因为逻辑需更复杂处理 overlapping periods

    def __str__(self):
        return (f"{self.user.username} 预订 {self.space.name} "
                f"从 {self.start_time.strftime('%Y-%m-%d %H:%M')} 到 {self.end_time.strftime('%H:%M')} "
                f"[{self.get_status_display()}]")

    def clean(self):
        """
        在保存前执行自定义验证，例如检查时间段的有效性。
        """
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            from django.core.exceptions import ValidationError
            raise ValidationError('结束时间必须晚于开始时间。')

        # 更多针对时间段重叠的验证应放在视图或序列化器中，因为它涉及到查询数据库
        # 且可能需要考虑状态为 'APPROVED' 的预订。


# ====================================================================
# Violation Model (违约记录)
# ====================================================================
class Violation(models.Model):
    """
    记录用户违约行为的模型。
    """
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
        on_delete=models.SET_NULL,  # 如果预订被删除，违约记录保留但关联关系删除
        null=True,
        blank=True,
        related_name='violation_records',  # 改为records以防与Booking的单一violation字段冲突
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

    def __str__(self):
        return (f"违约记录 ({self.get_violation_type_display()}) - "
                f"用户: {self.user.username} - "
                f"时间: {self.issued_at.strftime('%Y-%m-%d %H:%M')}")

    def save(self, *args, **kwargs):
        """
        重写 save 方法，在保存违约记录时更新 CustomUser 的 total_violation_count。
        """
        is_new = self._state.adding  # 判断是否为新创建的实例
        super().save(*args, **kwargs)
        if is_new:  # 只有新创建的违约记录才增加总违约次数
            self.user.total_violation_count += self.penalty_points
            self.user.save(update_fields=['total_violation_count'])