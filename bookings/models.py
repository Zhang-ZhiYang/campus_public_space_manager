# bookings/models.py
from django.db import models
from django.db.models import Manager, Index
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, post_delete, pre_save  # 引入Django信号
from django.dispatch import receiver
from django.utils import timezone  # 导入 timezone

# 从其他应用导入相关模型
from users.models import CustomUser
from spaces.models import Space, BookableAmenity, SpaceType

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
    ('CHECKED_IN', '已签到'),  # 新增
    ('CHECKED_OUT', '已签出'),  # 新增
)

# ====================================================================
# Violation 类型选择项
# ====================================================================
VIOLATION_TYPE_CHOICES = (
    ('NO_SHOW', '未到场'),
    ('LATE_CANCELLATION', '迟取消'),
    ('MISUSE_SPACE', '违规使用'),
    ('DAMAGE_PROPERTY', '设施损坏或超员使用'),  # 合并常见的违规类型描述
    ('OCCUPY_OVERTIME', '超时占用'),
    ('OTHER', '其他'),
)


# ====================================================================
# Booking Model (预订)
# ====================================================================
class Booking(models.Model):
    """
    用户预订特定空间或空间内特定可预订设施的模型。
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
        permissions = (
            ("can_approve_booking", "Can approve/reject any booking"),  # 可以批准/拒绝任何预订（全局）
            ("can_check_in_booking", "Can check in/out any booking"),  # 可以签到/签出任何预订（全局）
        )
        indexes = [
            Index(fields=['user', 'start_time']),
            Index(fields=['space', 'start_time', 'end_time']),
            Index(fields=['bookable_amenity', 'start_time', 'end_time']),
            Index(fields=['status']),
            Index(fields=['start_time']),
            Index(fields=['end_time']),
        ]

    def __str__(self):
        target_name = "未知目标"
        if self.space:
            target_name = self.space.name
        elif self.bookable_amenity:
            target_name = f"{self.bookable_amenity.space.name} 的 {self.bookable_amenity.amenity.name}"

        return (f"{self.user.get_full_name} 预订 {target_name} ({self.booked_quantity}个) "
                f"从 {self.start_time.strftime('%Y-%m-%d %H:%M')} 到 {self.end_time.strftime('%H:%M')} "
                f"[{self.get_status_display()}]")

    def clean(self):
        """
        在保存前执行自定义验证。
        - 确保开始时间早于结束时间。
        - 确保只能预订 Space 或 BookableAmenity 中的一个。
        - 校验 booked_quantity。
        """
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({'end_time': '结束时间必须晚于开始时间。'})

        if not ((self.space is not None) ^ (self.bookable_amenity is not None)):
            raise ValidationError('预订必须且只能指定一个目标：空间或设施实例。')

        if self.bookable_amenity:
            if self.booked_quantity <= 0:
                raise ValidationError({'booked_quantity': '预订设施时，预订数量必须大于0。'})
            if self.booked_quantity > self.bookable_amenity.quantity:
                raise ValidationError(
                    {'booked_quantity': f"预订数量不能超过设施总数量 {self.bookable_amenity.quantity}。"}
                )
            if not self.bookable_amenity.is_active or not self.bookable_amenity.is_bookable:
                raise ValidationError("所选设施不可预订或未启用。")

        if self.space:
            if self.booked_quantity != 1:
                raise ValidationError({'booked_quantity': '预订整个空间时，数量必须为1。'})
            if not self.space.is_active or not self.space.is_bookable:
                raise ValidationError("所选空间不可预订或未启用。")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


# ====================================================================
# Violation Model (违约记录)
# ====================================================================
class Violation(models.Model):
    """
    用户的违约记录。
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
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='violation_records',
        verbose_name="关联预订",
        help_text="与本次违约行为相关的预订（可选）"
    )
    space_type = models.ForeignKey(  # 新增字段，用于匹配禁用策略
        SpaceType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='violations',
        verbose_name="违约所属空间类型",
        help_text="本次违约行为发生时的空间类型，用于计算不同类型空间的违约点数"
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
        default=1,  # 默认扣1分
        verbose_name="违约点数",
        help_text="本次违约行为增加的违约点数"
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
        help_text="本次违约是否已经处理或解决。已解决的违规不计入活跃点数。"
    )
    resolved_at = models.DateTimeField(  # 新增
        null=True,
        blank=True,
        verbose_name="解决时间",
        help_text="本次违约被标记为已解决的时间"
    )
    resolved_by = models.ForeignKey(  # 新增
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_violations',
        verbose_name="解决人员",
        help_text="将本次违约标记为已解决的管理员/空间管理员"
    )

    class Meta:
        verbose_name = '违约记录'
        verbose_name_plural = verbose_name
        ordering = ['-issued_at']
        permissions = (
            ("can_resolve_violation", "Can resolve any violation"),  # 可以解决任何违规（全局）
        )
        indexes = [
            Index(fields=['user']),
            Index(fields=['booking']),
            Index(fields=['space_type']),
            Index(fields=['violation_type']),
            Index(fields=['issued_at']),
            Index(fields=['is_resolved']),
        ]

    def __str__(self):
        status_text = " (已解决)" if self.is_resolved else ""
        return (f"违约记录 ({self.get_violation_type_display()}){status_text} - "
                f"用户: {self.user.get_full_name} - "
                f"时间: {self.issued_at.strftime('%Y-%m-%d %H:%M')}")


# ====================================================================
# UserPenaltyPointsPerSpaceType Model (用户违约点数统计 - 按空间类型)
# ====================================================================
class UserPenaltyPointsPerSpaceType(models.Model):
    """
    记录用户在特定空间类型下的当前活跃违约点数。
    这些点数用于触发禁用策略。
    """
    objects: Manager = Manager()

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='penalty_points_records',
        verbose_name="用户"
    )
    # space_type 为空表示全局违约点数
    space_type = models.ForeignKey(
        SpaceType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='user_penalty_points',
        verbose_name="空间类型"
    )
    current_penalty_points = models.PositiveIntegerField(
        default=0,
        verbose_name="当前活跃违约点数",
        help_text="用户在此空间类型下当前未解决的累计违约点数，用于触发禁用"
    )
    last_violation_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="最后违约时间",
        help_text="该空间类型下最后一次违规记录的时间"
    )
    last_ban_trigger_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="最后触发禁用时间",
        help_text="该空间类型下最后一次点数触发禁用策略的时间"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = '用户违约点数 (按类型)'
        verbose_name_plural = verbose_name
        # 同一用户在同一空间类型下只能有一条记录
        unique_together = ('user', 'space_type')
        ordering = ['user__username', 'space_type__name']
        indexes = [
            Index(fields=['user']),
            Index(fields=['space_type']),
            Index(fields=['current_penalty_points']),
        ]

    def __str__(self):
        space_type_name = self.space_type.name if self.space_type else "全局"
        return (f"{self.user.get_full_name} 在 {space_type_name} 类型下 "
                f"当前活跃点数: {self.current_penalty_points}")


# ====================================================================
# SpaceTypeBanPolicy Model (空间类型禁用策略)
# ====================================================================
class SpaceTypeBanPolicy(models.Model):
    """
    定义根据违约点数自动禁用用户的策略。
    策略可以针对特定的空间类型或全局生效。
    """
    objects: Manager = Manager()

    # space_type 为空表示全局禁用策略
    space_type = models.ForeignKey(
        SpaceType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='ban_policies',
        verbose_name="策略应用空间类型",
        help_text="此禁用策略应用的特定空间类型；为空则表示全局策略"
    )
    threshold_points = models.PositiveIntegerField(
        verbose_name="触发点数阈值",
        help_text="当用户在此空间类型（或全局）的活跃点数达到此值时，触发禁用"
    )
    ban_duration = models.DurationField(
        verbose_name="禁用持续时长",
        help_text="达到阈值时的禁用时长（如：7天、30天等）"
    )
    priority = models.PositiveIntegerField(
        default=0,
        verbose_name="策略优先级",
        help_text="当多个策略满足条件时，数字越大优先级越高（0为最低）"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="是否启用此策略",
        help_text="此禁用策略是否处于启用状态"
    )
    description = models.TextField(
        blank=True,
        verbose_name="策略描述"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = '空间类型禁用策略'
        verbose_name_plural = verbose_name
        # 同一空间类型下，点数阈值不能重复
        unique_together = ('space_type', 'threshold_points')
        # 排序：全局策略优先（space_type为null），然后按空间类型名，最后按点数阈值降序和优先级降序
        # ordering = ['space_type_id__isnull', 'space_type__name', '-threshold_points', '-priority']
        indexes = [
            Index(fields=['space_type', 'threshold_points']),
            Index(fields=['is_active']),
        ]

    def __str__(self):
        space_type_name = self.space_type.name if self.space_type else "全局"
        return (
            f"{space_type_name} 达到 {self.threshold_points} 点禁用 {self.ban_duration} ({'启用' if self.is_active else '禁用'})")


# ====================================================================
# UserSpaceTypeBan Model (用户空间类型禁用记录)
# ====================================================================
class UserSpaceTypeBan(models.Model):
    """
    记录用户被禁用的具体时间段和原因。
    """
    objects: Manager = Manager()

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='activity_bans',
        verbose_name="被禁用用户"
    )
    # space_type 为空表示全局禁用
    space_type = models.ForeignKey(
        SpaceType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='user_bans',
        verbose_name="禁用空间类型",
        help_text="用户在此空间类型下被禁用；为空则表示全局禁用"
    )
    start_date = models.DateTimeField(verbose_name="禁用开始时间")
    end_date = models.DateTimeField(verbose_name="禁用结束时间")
    ban_policy_applied = models.ForeignKey(
        SpaceTypeBanPolicy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="应用禁用策略",
        help_text="触发本次禁用的具体策略（如果是由策略自动触发）"
    )
    reason = models.TextField(
        blank=True,
        verbose_name="禁用原因",
        help_text="对用户被禁用原因的说明"
    )
    issued_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issued_bans',
        verbose_name="执行禁用人员",
        help_text="执行本次禁用的管理员（可以是系统自动，也可以是手动）"
    )
    issued_at = models.DateTimeField(auto_now_add=True, verbose_name="禁用创建时间")

    class Meta:
        verbose_name = '用户禁用记录'
        verbose_name_plural = verbose_name
        ordering = ['-start_date']
        indexes = [
            Index(fields=['user']),
            Index(fields=['space_type']),
            Index(fields=['start_date', 'end_date']),
            Index(fields=['issued_at']),
        ]
        # 同一时间，一个用户在同一空间类型下只能有一个活跃的禁用记录
        # 这需要在 clean 或 service 层进行复杂校验，因为涉及时间重叠

    def __str__(self):
        space_type_name = self.space_type.name if self.space_type else "全局"
        status = "（已结束）" if self.end_date < timezone.now() else "（活跃中）"
        return (f"{self.user.get_full_name} 在 {space_type_name} 类型下被禁用 "
                f"从 {self.start_date.strftime('%Y-%m-%d')} 到 {self.end_date.strftime('%Y-%m-%d')} {status}")


# ====================================================================
# UserSpaceTypeExemption Model (用户空间类型豁免 - 白名单) - 从 users.models.py 移动到这里
# ====================================================================
class UserSpaceTypeExemption(models.Model):
    """
    用户在特定空间类型下的豁免记录（白名单）。
    可用于豁免某些预订规则、违规惩罚等。
    """
    objects: Manager = Manager()

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='exemptions',
        verbose_name="豁免用户"
    )
    space_type = models.ForeignKey(
        SpaceType,  # 直接引用本应用或已导入的 SpaceType
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='user_exemptions',
        verbose_name="豁免空间类型"
    )
    exemption_reason = models.TextField(
        verbose_name="豁免原因",
        help_text="说明用户获得豁免的具体原因"
    )
    start_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="豁免开始时间",
        help_text="豁免的起始时间点，为空表示永久有效"
    )
    end_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="豁免结束时间",
        help_text="豁免的结束时间点，为空表示永久有效"
    )
    granted_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='granted_exemptions',

        verbose_name="授权人员"
    )
    granted_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="授权时间"
    )

    class Meta:
        verbose_name = '用户豁免记录'
        verbose_name_plural = verbose_name
        # 同一用户在同一空间类型下只能有一条活跃的豁免记录
        # 如果存在时间范围，需要通过 clean 方法进行更复杂的校验。或者允许 overlap，以最后一条生效。
        # 为简化，这里采用 unique_together 强制一个用户在特定空间类型下只有一条豁免。
        unique_together = ('user', 'space_type')
        ordering = ['user__username', 'space_type__name']
        indexes = [
            Index(fields=['user']),
            Index(fields=['space_type']),
            Index(fields=['start_date', 'end_date']),
        ]

    def __str__(self):
        space_type_name = self.space_type.name if self.space_type else "全局"
        return f"{self.user.get_full_name} 豁免 {space_type_name}"


# ====================================================================
# Django Signals for Violation and UserPenaltyPointsPerSpaceType
# ====================================================================

@receiver(pre_save, sender=Violation)
def store_old_violation_is_resolved(sender, instance, **kwargs):
    """在保存前存储旧的 is_resolved 状态，以便 post_save 判断其是否改变。"""
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            instance._old_is_resolved = old_instance.is_resolved
        except sender.DoesNotExist:
            instance._old_is_resolved = False
    else:
        instance._old_is_resolved = False


@receiver(post_save, sender=Violation)
def update_user_penalty_points_on_violation_save(sender, instance, created, **kwargs):
    """
    在 Violation 实例创建、更新后，更新 UserPenaltyPointsPerSpaceType 的活跃点数，并考虑解决状态。
    """
    if instance.user:
        # 1. 确保 Violation 的 space_type 总是被填充
        # 如果 Violation 没有直接关联 space_type，尝试从 booking 获取
        if not instance.space_type and instance.booking:
            if instance.booking.space and instance.booking.space.space_type:
                instance.space_type = instance.booking.space.space_type
            elif instance.booking.bookable_amenity and instance.booking.bookable_amenity.space and instance.booking.bookable_amenity.space.space_type:
                instance.space_type = instance.booking.bookable_amenity.space.space_type

            # 重要：如果在这个信号中修改了 instance.space_type，需要重新保存 instance
            # 但在 post_save 中修改自身并保存容易导致无限循环。
            # 更好的做法是在 Violation 的 save 或 clean 方法中确保 space_type 的正确性。
            # 为了避免信号中的无限循环，这里假定 space_type 在进入 post_save 时是正确的或 None。
            # 如果 space_type 在这里被赋值，但并未保存，则后续 penalty_points_record 的 space_type 可能会丢失。
            # 简化处理：如果 space_type 依然为 None，则视为全局点数 (space_type=None)

        target_space_type_for_points = instance.space_type  # 此时可以是 None for global points

        penalty_points_record, created_record = UserPenaltyPointsPerSpaceType.objects.get_or_create(
            user=instance.user,
            space_type=target_space_type_for_points  # 可以是 None
        )

        old_is_resolved = getattr(instance, '_old_is_resolved', False)
        points_changed = 0

        if created:  # 新创建的违规
            if not instance.is_resolved:
                points_changed = instance.penalty_points
        else:  # 更新的违规
            if instance.is_resolved and not old_is_resolved:  # 从未解决变为已解决
                points_changed = -instance.penalty_points
            elif not instance.is_resolved and old_is_resolved:  # 从已解决变为未解决
                points_changed = instance.penalty_points
            # TODO: 如果 penalty_points 自身值改变，则需要更复杂的逻辑，这里假设 penalty_points 除非创建，否则不变

        if points_changed != 0:
            penalty_points_record.current_penalty_points = max(0,
                                                               penalty_points_record.current_penalty_points + points_changed)
            penalty_points_record.last_violation_at = instance.issued_at
            penalty_points_record.save()  # 保存点数记录

            # 检查是否触发禁用策略 (仅在活跃点数发生变化时才检查)
            check_for_ban_trigger(penalty_points_record)


@receiver(post_delete, sender=Violation)
def update_user_penalty_points_on_violation_delete(sender, instance, **kwargs):
    """
    在 Violation 实例被删除后，减少 UserPenaltyPointsPerSpaceType 的活跃点数。
    """
    if instance.user:
        target_space_type = instance.space_type
        if not target_space_type and instance.booking and instance.booking.space:
            target_space_type = instance.booking.space.space_type
        elif not target_space_type and instance.booking and instance.booking.bookable_amenity and instance.booking.bookable_amenity.space:
            target_space_type = instance.booking.bookable_amenity.space.space_type

        # 如果无法确定 space_type，则视为全局
        # if not target_space_type:
        # pass # 可以在这里决定是否将删除的违规点数从全局记录中扣除

        try:
            penalty_points_record = UserPenaltyPointsPerSpaceType.objects.get(
                user=instance.user,
                space_type=target_space_type  # 可以是 None
            )
            if not instance.is_resolved:  # 仅未解决的违规删除时才减少活跃点数
                penalty_points_record.current_penalty_points = max(0,
                                                                   penalty_points_record.current_penalty_points - instance.penalty_points)
                penalty_points_record.last_violation_at = timezone.now()  # 更新最后违规时间
                penalty_points_record.save()
            check_for_ban_trigger(penalty_points_record)
        except UserPenaltyPointsPerSpaceType.DoesNotExist:
            pass


def check_for_ban_trigger(penalty_points_record: 'UserPenaltyPointsPerSpaceType'):
    """
    检查用户的活跃违约点数是否达到禁用策略的阈值，并创建/更新禁用记录。
    此函数应在 UserPenaltyPointsPerSpaceType 更新后被调用。
    """

    # 查找适用于该空间类型（或全局）且处于启用状态的策略
    applicable_policies = SpaceTypeBanPolicy.objects.filter(
        models.Q(space_type=penalty_points_record.space_type) | models.Q(space_type__isnull=True),
        is_active=True,
        threshold_points__lte=penalty_points_record.current_penalty_points
    ).order_by('-threshold_points', '-priority')  # 优先匹配点数最高、优先级最高的策略

    if applicable_policies.exists():
        policy = applicable_policies.first()  # 选择最匹配的策略
        ban_start = timezone.now()
        ban_end = ban_start + policy.ban_duration

        # 检查是否已存在一个当前活跃的禁用记录，如果存在且新的禁用时间更长，则更新
        existing_active_ban = UserSpaceTypeBan.objects.filter(
            user=penalty_points_record.user,
            space_type=penalty_points_record.space_type,  # 针对特定空间类型或全局
            end_date__gt=timezone.now()  # 仍在活跃期
        ).first()

        if existing_active_ban:
            if ban_end > existing_active_ban.end_date:  # 如果新策略的禁用时长更长
                existing_active_ban.end_date = ban_end
                existing_active_ban.ban_policy_applied = policy
                existing_active_ban.reason = f"因在 {penalty_points_record.space_type.name if penalty_points_record.space_type else '全局'} 累计 {policy.threshold_points} 点触发，更新禁用"
                existing_active_ban.save(update_fields=['end_date', 'ban_policy_applied', 'reason'])
        else:
            UserSpaceTypeBan.objects.create(
                user=penalty_points_record.user,
                space_type=penalty_points_record.space_type,  # 可为 None
                start_date=ban_start,
                end_date=ban_end,
                ban_policy_applied=policy,
                reason=f"因在 {penalty_points_record.space_type.name if penalty_points_record.space_type else '全局'} 累计 {policy.threshold_points} 点触发禁用",
                issued_by=None  # 标记为系统自动触发
            )
        penalty_points_record.last_ban_trigger_at = ban_start
        penalty_points_record.save(update_fields=['last_ban_trigger_at'])