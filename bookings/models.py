# bookings/models.py
from django.db import models
from django.db.models import Manager
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone  # 导入 timezone 用于时间操作

# 从其他应用导入相关模型 (CustomUser 和 Role)
try:
    from users.models import CustomUser, Role
except ImportError:
    # 临时代替 CustomUser 和 Role，用于让模型定义通过
    class CustomUser(models.Model):
        username = models.CharField(max_length=150, unique=True)
        total_violation_count = models.PositiveIntegerField(default=0)
        # 模拟 is_superuser 和 is_admin 属性，确保 Booking.clean() 中的豁免逻辑能正常解析
        is_superuser = False
        is_admin = False

        # 模拟 role 属性，确保 Booking.clean() 中的 self.user.role 不会直接报错
        class _MockRole:
            pk = 0
            name = "Mock Role"

            def __str__(self): return self.name

        role = _MockRole()

        def save(self, *args, **kwargs):
            pass  # 模拟 save 方法，防止信号处理时报错


    class Role(models.Model):  # 模拟 Role 模型
        name = models.CharField(max_length=50, unique=True)

        def __str__(self): return self.name


    print("Warning: users.models.CustomUser or Role could not be imported. Using mock classes for model definition. "
          "Ensure 'users' app is in INSTALLED_APPS and CustomUser/Role models are correctly defined.")

# 导入 Space 和 BookableAmenity
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
        indexes = [
            models.Index(fields=['user', 'start_time', 'end_time']),
            models.Index(fields=['space', 'start_time', 'end_time']),
            models.Index(fields=['bookable_amenity', 'start_time', 'end_time']),
            models.Index(fields=['status']),
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
                f"从 {self.start_time.strftime('%Y-%m-%d %H:%M')} 到 {self.end_time.strftime('%Y-%m-%d %H:%M')} "
                f"[{self.get_status_display()}]")

    def clean(self):
        super().clean()

        # 1. 确保用户和预订目标存在
        if not self.user:
            raise ValidationError('预订用户不能为空。')
        if not ((self.space is not None) ^ (self.bookable_amenity is not None)):
            raise ValidationError('预订必须且只能指定一个目标：空间或设施实例。')

        user_role = self.user.role

        # --- 管理员豁免逻辑 ---
        # 允许超级用户和系统管理员绕过此类限制
        if self.user and hasattr(self.user, 'is_admin') and self.user.is_admin:
            # 管理员用户跳过所有角色限制和部分激活/可预订检查
            pass
        else:  # 普通用户需要进行所有检查
            # 角色必须存在
            if not user_role:
                raise ValidationError('预订用户必须分配一个角色。')

            # =========================================================================
            # !!! 核心修改在此处：根据预订目标类型进行不同的角色限制检查 !!!
            # =========================================================================

            if self.space:  # 预订的是整个空间
                # 检查空间本身的角色预订限制 (黑名单)
                if not self.space.can_role_book(user_role):
                    raise ValidationError(f"您的角色 ({user_role.name}) 不允许预订空间 '{self.space.name}'。")

                # 空间自身必须活跃且可预订
                if not self.space.is_active or not self.space.is_bookable:
                    raise ValidationError(f"所选空间 '{self.space.name}' 不可预订或未启用。")

                # 预订整个空间数量必须为1
                if self.booked_quantity != 1:
                    raise ValidationError({'booked_quantity': '预订整个空间时，数量必须为1。'})

                # 确定用于时间/冲突检查的目标空间
                target_for_conflict_check = self.space

            elif self.bookable_amenity:  # 预订的是设施实例
                # 根据新需求，预订设施时，不检查父级空间的 restricted_roles
                # 也就是说，如果学生被禁止预订教室，但可以预订教室内的投影仪。

                # 检查设施实例自身是否活跃且可预订
                if not self.bookable_amenity.is_active or not self.bookable_amenity.is_bookable:
                    raise ValidationError(
                        f"所选设施 '{self.bookable_amenity.amenity.name}' (in {self.bookable_amenity.space.name}) 不可预订或未启用。")

                # 设施所属的父级空间必须是活跃的 (即使不检查角色限制，空间不活跃设施也无法用)
                if not self.bookable_amenity.space.is_active:
                    raise ValidationError(
                        f"设施所属空间 '{self.bookable_amenity.space.name}' 未激活，因此无法预订其设施。")

                # 校验设施预订数量
                if self.booked_quantity <= 0:
                    raise ValidationError({'booked_quantity': '预订设施时，预订数量必须大于0。'})
                if self.booked_quantity > self.bookable_amenity.quantity:
                    raise ValidationError(
                        {'booked_quantity': f"预订数量不能超过设施总数量 {self.bookable_amenity.quantity}。"})

                # 确定用于时间/冲突检查的目标空间实例是设施的父级空间
                target_for_conflict_check = self.bookable_amenity.space

            else:  # 防御性编程，如果space和bookable_amenity都为空，虽然前面已经检查过
                raise ValidationError('无法确定预订目标。')

        # 此时 target_for_conflict_check 变量已经确定 (无论是直接预订空间还是预订设施内的父空间)
        # 后面的时间、时长和冲突检查都将使用这个 target_for_conflict_check 变量

        # 2. 预订时间逻辑检查
        if self.start_time >= self.end_time:
            raise ValidationError({'end_time': '结束时间必须晚于开始时间。'})

        now = timezone.now()
        # 允许修改已存在且已开始的预订，但不能创建或修改开始时间为过去的预订
        if self.start_time < now:
            # 如果是新创建的预订，或者旧预订的开始时间被修改到过去，则报错
            # 这里的逻辑可以更细致：如果预订已开始，不应该允许修改 start_time 和 end_time
            if not self.pk or (self.pk and Booking.objects.get(pk=self.pk).start_time >= now):
                raise ValidationError({'start_time': '预订开始日期不能在过去。'})

        # 3. 目标空间时间范围和时长检查 (现在统一使用 target_for_conflict_check)
        space_start_time = target_for_conflict_check.available_start_time
        space_end_time = target_for_conflict_check.available_end_time

        booking_start_time_only = self.start_time.astimezone(
            timezone.get_default_timezone()).time() if self.start_time.tzinfo else self.start_time.time()
        booking_end_time_only = self.end_time.astimezone(
            timezone.get_default_timezone()).time() if self.end_time.tzinfo else self.end_time.time()

        # 检查预订时间是否在空间/设施父级空间的每日可用时间范围内
        if not (space_start_time <= booking_start_time_only and booking_end_time_only <= space_end_time):
            raise ValidationError(f"预订时间必须在目标空间 '{target_for_conflict_check.name}' 的每日可预订时间范围 "
                                  f"({space_start_time.strftime('%H:%M')} - {space_end_time.strftime('%H:%M')}) 内。")

        booking_duration = self.end_time - self.start_time
        if booking_duration < target_for_conflict_check.min_booking_duration:
            raise ValidationError(
                f"预订时长必须至少为 {target_for_conflict_check.min_booking_duration.total_seconds() / 60} 分钟。")
        if booking_duration > target_for_conflict_check.max_booking_duration:
            raise ValidationError(
                f"预订时长不能超过 {target_for_conflict_check.max_booking_duration.total_seconds() / 60} 分钟。")

        # 4. 与现有预订的冲突检查 (包括缓冲时间)
        buffer_timedelta = timedelta(minutes=target_for_conflict_check.buffer_time_minutes)

        # 筛选出与当前预订有时间上重叠可能的所有其他预订
        # 注意：这里因为 target_for_conflict_check 现在是统一的父空间，所以查询条件也统一
        conflicting_bookings_query = Booking.objects.filter(
            space=target_for_conflict_check,  # 确保这里的 space 字段匹配
            start_time__lt=self.end_time + buffer_timedelta,
            end_time__gt=self.start_time - buffer_timedelta,
        ).exclude(
            pk=self.pk
        ).exclude(
            status__in=['REJECTED', 'CANCELLED']
        )

        if self.bookable_amenity:  # 如果预订的是设施
            # 过滤出针对同一个 BookableAmenity 的冲突预订
            conflicting_amenity_bookings = conflicting_bookings_query.filter(bookable_amenity=self.bookable_amenity)
            booked_quantity_in_slot = sum(b.booked_quantity for b in conflicting_amenity_bookings)
            if booked_quantity_in_slot + self.booked_quantity > self.bookable_amenity.quantity:
                raise ValidationError(
                    f"设施 '{self.bookable_amenity.amenity.name}' (in {self.bookable_amenity.space.name}) 在该时间段库存不足。"
                    f"当前可用: {self.bookable_amenity.quantity - booked_quantity_in_slot}。"
                )
        else:  # 如果预订的是整个空间
            # 检查是否有任何冲突的预订，无论它们是针对整个空间还是空间内的设施
            # 这可能需要更细致的逻辑：预订整个空间时，应该检查是否与任何设施的预订冲突
            # 反之，预订设施时，不应与整个空间的预订冲突
            # 简化起见，我们假设一旦空间被整体预订，内部设施也隐含被占用。
            # 或者反过来，只有当设施全部被预订，且所有设施加起来等于空间容量，才算空间满了。
            # 但目前策略是：预订整个空间，会与该空间所有其他任何预订冲突。

            # 如果是整个空间的预订，那么与任何其他预订其内部设施或自身的预订都冲突
            if conflicting_bookings_query.exists():
                raise ValidationError(
                    f"空间 '{target_for_conflict_check.name}' 在该时间段已被预订，且与前后预订存在冲突或缓冲时间不足。")

    def save(self, *args, **kwargs):
        self.full_clean()  # 确保在保存前调用所有 clean 方法
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
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['booking']),
            models.Index(fields=['violation_type']),
            models.Index(fields=['issued_at']),
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
        # 仅更新指定字段，避免触发 CustomUser 的 clean/save 逻辑中的其他副作用
        instance.user.save(update_fields=['total_violation_count'])


@receiver(post_delete, sender=Violation)
def update_user_violation_count_on_delete(sender, instance, **kwargs):
    """
    在 Violation 实例被删除后，减少用户总违约次数。
    """
    instance.user.total_violation_count = max(0, instance.user.total_violation_count - instance.penalty_points)
    # 仅更新指定字段
    instance.user.save(update_fields=['total_violation_count'])