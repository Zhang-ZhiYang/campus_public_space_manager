# bookings/models.py
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import Manager, Index  # 导入 Manager, Index
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

from core.utils.date_utils import validate_booking_time_integrity, validate_booking_duration, \
    validate_booking_daily_availability
# CRITICAL FIX: 移除 setup_perm_query_set 的导入，以及所有自定义 PermManager 和 PermQuerySet 的定义

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
    ('CHECKED_IN', '已签到'),
    ('CHECKED_OUT', '已签出'),
)

# ====================================================================
# Violation 类型选择项
# ====================================================================
VIOLATION_TYPE_CHOICES = (
    ('NO_SHOW', '未到场'),
    ('LATE_CANCELLATION', '迟取消'),
    ('MISUSE_SPACE', '违规使用'),
    ('DAMAGE_PROPERTY', '设施损坏或超员使用'),
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
    # CRITICAL FIX: 使用默认的 models.Manager
    objects = models.Manager()

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
            ("can_approve_booking", "Can approve/reject any booking"),
            ("can_check_in_booking", "Can check in/out any booking"),
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
            amenity_val = getattr(self.bookable_amenity, 'amenity', None)
            space_val = getattr(self.bookable_amenity, 'space', None)
            amenity_name = amenity_val.name if amenity_val else "未知设施类型"
            space_name = space_val.name if space_val else "未知空间"
            return f"{self.user.get_full_name} 预订 设施: {amenity_name} in {space_name} ({self.booked_quantity}个) 从 {self.start_time.strftime('%Y-%m-%d %H:%M')} 到 {self.end_time.strftime('%H:%M')} [{self.get_status_display()}]"

        return (f"{self.user.get_full_name} 预订 {target_name} ({self.booked_quantity}个) "
                f"从 {self.start_time.strftime('%Y-%m-%d %H:%M')} 到 {self.end_time.strftime('%H:%M')} "
                f"[{self.get_status_display()}]")

    def clean(self):
        super().clean()

        if not ((self.space is not None) ^ (self.bookable_amenity is not None)):
            raise ValidationError('预订必须且只能指定一个目标：空间或设施实例。')

        # 确定预订目标
        target = self.space if self.space else self.bookable_amenity.space if self.bookable_amenity else None

        if not target:
            raise ValidationError('无法确定预订目标。')

        # 获取 SpaceType 规则
        space_type = target.space_type
        if not space_type:
            raise ValidationError('预订目标没有关联的空间类型。')

        effective_min_duration = target.min_booking_duration
        effective_max_duration = target.max_booking_duration
        effective_available_start_time = target.available_start_time
        effective_available_end_time = target.available_end_time
        # 1. 校验预订时间的完整性
        validate_booking_time_integrity(self.start_time, self.end_time)

        # 2. 校验预订时长
        validate_booking_duration(self.start_time, self.end_time,
                                  effective_min_duration, effective_max_duration)

        # 3. 校验预订时间范围（每日可用时间）
        validate_booking_daily_availability(self.start_time, self.end_time,
            effective_available_start_time, effective_available_end_time)
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

        if not self.user:
            raise ValidationError("预订用户不能为空。")

        target_space_type_for_ban = None
        if self.space:
            target_space_type_for_ban = self.space.space_type
        elif self.bookable_amenity and self.bookable_amenity.space:
            target_space_type_for_ban = self.bookable_amenity.space.space_type

        if not target_space_type_for_ban:
            pass  # 目标校验已在前面完成

        active_bans = UserSpaceTypeBan.objects.filter(
            user=self.user,
            end_date__gt=timezone.now()
        )

        global_ban_record = active_bans.filter(space_type__isnull=True).first()
        if global_ban_record:
            raise ValidationError(
                f"您已被全站禁用，直到 {global_ban_record.end_date.strftime('%Y-%m-%d %H:%M')}。原因: {global_ban_record.reason}"
            )

        if target_space_type_for_ban:
            specific_space_type_ban_record = active_bans.filter(space_type=target_space_type_for_ban).first()
            if specific_space_type_ban_record:
                raise ValidationError(
                    f"您已被禁止预订 '{target_space_type_for_ban.name}' 类型的空间，直到 {specific_space_type_ban_record.end_date.strftime('%Y-%m-%d %H:%M')}。原因: {specific_space_type_ban_record.reason}"
                )

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
    # CRITICAL FIX: 使用默认的 models.Manager
    objects = models.Manager()

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
    space_type = models.ForeignKey(
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
        default=1,
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
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="解决时间",
        help_text="本次违约被标记为已解决的时间"
    )
    resolved_by = models.ForeignKey(
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
            ("can_resolve_violation", "Can resolve any violation"),
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
    # CRITICAL FIX: 使用默认的 models.Manager
    objects = models.Manager()

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='penalty_points_records',
        verbose_name="用户"
    )
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
    # CRITICAL FIX: 使用默认的 models.Manager
    objects = models.Manager()

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
        unique_together = ('space_type', 'threshold_points')
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
    # CRITICAL FIX: 使用默认的 models.Manager
    objects = models.Manager()

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='activity_bans',
        verbose_name="被禁用用户"
    )
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

    def __str__(self):
        space_type_name = self.space_type.name if self.space_type else "全局"
        status = "（已结束）" if self.end_date < timezone.now() else "（活跃中）"
        return (f"{self.user.get_full_name} 在 {space_type_name} 类型下被禁用 "
                f"从 {self.start_date.strftime('%Y-%m-%d')} 到 {self.end_date.strftime('%Y-%m-%d')} {status}")


# ====================================================================
# UserSpaceTypeExemption Model (用户空间类型豁免 - 白名单)
# ====================================================================
class UserSpaceTypeExemption(models.Model):
    """
    用户在特定空间类型下的豁免记录（白名单）。
    可用于豁免某些预订规则、违规惩罚等。
    """
    # CRITICAL FIX: 使用默认的 models.Manager
    objects = models.Manager()

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='exemptions',
        verbose_name="豁免用户"
    )
    space_type = models.ForeignKey(
        SpaceType,
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
# DailyBookingLimit Model (每日预订限制)
# ====================================================================
class DailyBookingLimit(models.Model):
    """
    定义不同用户组的每日最大预订次数限制。
    """
    objects = models.Manager()

    group = models.OneToOneField( # 使用 OneToOneField 确保每个组只有一条规则
        Group,
        on_delete=models.CASCADE,
        related_name='daily_booking_limit',
        verbose_name="用户组",
        help_text="此每日预订限制规则应用的用户组。"
    )
    max_bookings = models.PositiveIntegerField(
        default=0, # 0表示没有限制
        verbose_name="每日最大预订次数",
        help_text="该组用户每天最多可以进行的预订次数。设置为0表示没有限制。"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="是否启用此限制"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = '每日预订限制'
        verbose_name_plural = verbose_name
        ordering = ['group__name']
        indexes = [
            Index(fields=['group']),
            Index(fields=['is_active']),
        ]

    def __str__(self):
        limit_str = f"{self.max_bookings} 次" if self.max_bookings > 0 else "无限制"
        return f"{self.group.name} 每日预订限制: {limit_str} ({'启用' if self.is_active else '禁用'})"