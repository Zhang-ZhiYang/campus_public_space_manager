# bookings/models.py
from django.contrib.auth.models import Group  # 导入 Group 模型
from django.db import models
from django.db.models import Manager, Index  # 导入 Manager, Index
# 修正 datetime 导入：从标准库中导入 datetime 和 timedelta
from datetime import datetime, timedelta, date, time  # 新增 datetime, date, time
from django.core.exceptions import ValidationError
from django.utils import timezone
import uuid  # 导入 uuid 模块

# 导入外部模型
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

# 新增预订处理状态，用于异步流程
PROCESSING_STATUS_CHOICES = (
    ('SUBMITTED', '已提交'),  # 初始状态，等待异步处理
    ('IN_PROGRESS', '处理中'),  # 异步任务正在执行深度校验
    ('CREATED', '已创建'),  # 深度校验通过，Booking 记录已创建/更新为最终状态（PENDING/APPROVED）
    ('FAILED_VALIDATION', '校验失败'),  # 深度校验未通过
    ('FAILED_RUNTIME', '运行时错误'),  # 异步任务中发生未知错误
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
    objects = models.Manager()

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='bookings',
        verbose_name="预订用户",
        help_text="发起预订请求的用户"
    )

    # request_uuid 用于实现 API 幂等性，由客户端生成
    request_uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        null=False,  # 必须有，防止重复请求
        blank=False,
        verbose_name="请求唯一标识",
        help_text="用于标识一次预订请求的唯一UUID"
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

    # 新增 redundant 字段 related_space，方便查询和索引
    related_space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='all_related_bookings',
        verbose_name="关联父空间",
        help_text="此预订所属的实际空间（无论是直接预订空间本身还是其内部设施）",
    )
    expected_attendees = models.PositiveIntegerField(  # <-- 添加这个字段
        null=True, blank=True,
        verbose_name="预期参与人数",
        help_text="预订活动预期参与的人数，仅用于预订整个空间时检查容量。"
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
        help_text="预订的当前业务状态（例如：PENDING, APPROVED, CANCELLED）"
    )

    # 新增 processing_status，记录异步处理流程状态
    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default='SUBMITTED',
        verbose_name="处理状态",
        help_text="异步处理流程中预订的当前状态（例如：SUBMITTED, IN_PROGRESS, CREATED, FAILED_VALIDATION）"
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
        ordering = ['-created_at']  # 优先按创建时间排序会更合理
        permissions = (
            ("can_view_all_bookings", "Can view all bookings across all spaces"),
            ("can_create_booking", "Can create new bookings"),
            ("can_approve_any_booking", "Can approve/reject any booking in the system"),
            ("can_check_in_any_booking", "Can check-in/out any booking in the system"),
            ("can_cancel_any_booking", "Can cancel any booking in the system"),
            ("can_edit_any_booking_notes", "Can edit admin notes of any booking"),
            ("can_delete_any_booking", "Can delete any booking in the system"),
            ("can_mark_no_show_and_create_violation", "Can mark no-show and create violation"),
        )
        indexes = [
            Index(fields=['user', 'start_time']),
            Index(fields=['space', 'start_time', 'end_time']),
            Index(fields=['bookable_amenity', 'start_time', 'end_time']),
            Index(fields=['related_space', 'start_time', 'end_time']),  # 新增索引
            Index(fields=['status']),
            Index(fields=['processing_status']),  # 新增索引
            Index(fields=['request_uuid']),  # 新增索引
            Index(fields=['start_time']),
            Index(fields=['end_time']),
        ]

    def __str__(self):
        target_name = "未知目标"
        if self.space:
            target_name = self.space.name
        elif self.bookable_amenity and self.bookable_amenity.amenity:
            target_name = f"{self.bookable_amenity.amenity.name} in {self.bookable_amenity.space.name if self.bookable_amenity.space else 'Unknown Space'}"

        return (f"{self.user.get_full_name} 预订 {target_name} ({self.booked_quantity}个) "
                f"从 {self.start_time.strftime('%Y-%m-%d %H:%M')} 到 {self.end_time.strftime('%Y-%m-%d %H:%M')} "
                f"[{self.get_status_display()}] ({self.get_processing_status_display()})")

    def clean(self):
        """
        精简后的 clean 方法，只进行模型自身的数据完整性校验。
        所有复杂的业务逻辑（时间冲突、禁用、权限、每日限制等）已移至 Service 层。
        """
        super().clean()

        # 校验必须且只能指定一个目标：空间或设施实例。
        if not ((self.space is not None) ^ (self.bookable_amenity is not None)):
            raise ValidationError('预订必须且只能指定一个目标：空间或设施实例。')

        # 自动填充 related_space 字段
        if self.space:
            self.related_space = self.space
        elif self.bookable_amenity and self.bookable_amenity.space:
            self.related_space = self.bookable_amenity.space
        else:
            # 如果走到这里，说明 bookable_amenity 没有关联的 space，这是一个数据不一致。
            raise ValidationError('无法确定预订目标所属的空间。')

        # 预订数量校验
        if self.booked_quantity <= 0:
            raise ValidationError({'booked_quantity': '预订数量必须大于0。'})

        if self.bookable_amenity:
            # 预订设施时，数量不能超过设施总数量（这里进行初步校验，深度校验会再次确认）
            if self.bookable_amenity.quantity is not None and self.booked_quantity > self.bookable_amenity.quantity:
                raise ValidationError(
                    {'booked_quantity': f"预订数量不能超过设施总数量 {self.bookable_amenity.quantity}。"}
                )
        elif self.space:
            # 预订整个空间时，数量必须为1
            if self.booked_quantity != 1:
                raise ValidationError({'booked_quantity': '预订整个空间时，数量必须为1。'})

        if not self.user:
            raise ValidationError("预订用户不能为空。")

        # 基础时间顺序校验
        if self.start_time is None or self.end_time is None:
            raise ValidationError('预订的开始时间和结束时间不能为空。')
        if self.start_time >= self.end_time:
            raise ValidationError({'end_time': '结束时间必须晚于开始时间。'})

        # 仅对新创建的或未处理的预订进行“不能预订过去时间”的检查
        # 允许历史预订的存在及状态修改（例如取消历史记录、标记NO_SHOW）
        # `processing_status` 用于判断该 Booking 是否仍在“创建”流程中
        if self.processing_status in ['SUBMITTED', 'IN_PROGRESS'] and self.start_time < timezone.now():
            raise ValidationError({'start_time': '不能预订过去的时间。'})

    def save(self, *args, **kwargs):
        """在保存前调用 full_clean()
           注意：对于部分更新，kwargs.get('update_fields') 可以避免全量验证，
           但这里为了简化和模型自身的强一致性，暂时保留 full_clean()。
           Service 层将确保传入的数据是干净的。
        """
        self.full_clean()
        super().save(*args, **kwargs)

    def _get_related_object_dict(self, obj):
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict(include_related=False)  # 避免无限递归
        return {'id': obj.id, 'name': str(obj)} if obj else None

    # to_dict 方法，用于缓存
    def to_dict(self, include_related: bool = True) -> dict:
        data = {
            'id': self.id,
            'request_uuid': str(self.request_uuid),
            'booked_quantity': self.booked_quantity,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat(),
            'purpose': self.purpose,
            'status': self.status,
            'processing_status': self.processing_status,
            'admin_notes': self.admin_notes,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }
        if include_related:
            data['user'] = self._get_related_object_dict(self.user)
            data['space'] = self._get_related_object_dict(self.space)
            data['bookable_amenity'] = self._get_related_object_dict(self.bookable_amenity)
            data['related_space'] = self._get_related_object_dict(self.related_space)
            data['reviewed_by'] = self._get_related_object_dict(self.reviewed_by)
            if self.reviewed_at: data['reviewed_at'] = self.reviewed_at.isoformat()
        return data


# ====================================================================
# Violation Model (违约记录)
# ====================================================================
class Violation(models.Model):
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
            ("can_view_all_violations", "Can view all violation records"),
            ("can_create_violation_record", "Can create new violation records"),
            ("can_edit_violation_record", "Can edit any violation record"),
            ("can_delete_violation_record", "Can delete any violation record"),
            ("can_resolve_violation_record", "Can resolve any violation record"),
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

    def to_dict(self, include_related: bool = True) -> dict:
        data = {
            'id': self.id,
            'violation_type': self.violation_type,
            'description': self.description,
            'penalty_points': self.penalty_points,
            'issued_at': self.issued_at.isoformat(),
            'is_resolved': self.is_resolved,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
        }
        if include_related:
            data['user'] = self._get_related_object_dict(self.user)
            data['booking'] = self._get_related_object_dict(self.booking)
            data['space_type'] = self._get_related_object_dict(self.space_type)
            data['issued_by'] = self._get_related_object_dict(self.issued_by)
            data['resolved_by'] = self._get_related_object_dict(self.resolved_by)
        return data


# ====================================================================
# UserPenaltyPointsPerSpaceType Model (用户违约点数统计 - 按空间类型)
# ====================================================================
class UserPenaltyPointsPerSpaceType(models.Model):
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
        verbose_name="空间类型",
        help_text="特定空间类型下的点数，为空则表示全局点数"
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
        permissions = [
            ("can_view_penalty_points", "Can view user penalty points records"),
        ]
        indexes = [
            Index(fields=['user']),
            Index(fields=['space_type']),
            Index(fields=['current_penalty_points']),
        ]

    def __str__(self):
        space_type_name = self.space_type.name if self.space_type else "全局"
        return (f"{self.user.get_full_name} 在 {space_type_name} 类型下 "
                f"当前活跃点数: {self.current_penalty_points}")

    def _get_related_object_dict(self, obj):
        # Helper to get dict from related objects, avoiding infinite loop
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict(include_related=False)
        return {'id': obj.id, 'name': str(obj)} if obj else None

    def to_dict(self, include_related: bool = True) -> dict:
        data = {
            'id': self.id,
            'current_penalty_points': self.current_penalty_points,
            'last_violation_at': self.last_violation_at.isoformat() if self.last_violation_at else None,
            'last_ban_trigger_at': self.last_ban_trigger_at.isoformat() if self.last_ban_trigger_at else None,
            'updated_at': self.updated_at.isoformat(),
        }
        if include_related:
            data['user'] = self._get_related_object_dict(self.user)
            data['space_type'] = self._get_related_object_dict(self.space_type)
        return data


# ====================================================================
# SpaceTypeBanPolicy Model (空间类型禁用策略)
# ====================================================================
class SpaceTypeBanPolicy(models.Model):
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
        permissions = (
            ("can_view_ban_policies", "Can view space type ban policies"),
            ("can_manage_ban_policies", "Can create, edit, delete space type ban policies"),
        )

    def __str__(self):
        space_type_name = self.space_type.name if self.space_type else "全局"
        return (
            f"{space_type_name} 达到 {self.threshold_points} 点禁用 {self.ban_duration} ({'启用' if self.is_active else '禁用'})")

    def _get_related_object_dict(self, obj):
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict(include_related=False)
        return {'id': obj.id, 'name': str(obj)} if obj else None

    def to_dict(self, include_related: bool = True) -> dict:
        data = {
            'id': self.id,
            'threshold_points': self.threshold_points,
            'ban_duration': str(self.ban_duration),  # timedelta to string
            'priority': self.priority,
            'is_active': self.is_active,
            'description': self.description,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }
        if include_related:
            data['space_type'] = self._get_related_object_dict(self.space_type)
        return data


# ====================================================================
# UserSpaceTypeBan Model (用户空间类型禁用记录)
# ====================================================================
class UserSpaceTypeBan(models.Model):
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
        permissions = (
            ("can_view_user_bans", "Can view user ban records"),
            ("can_manage_user_bans", "Can create, edit, delete user ban records"),
        )
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

    def _get_related_object_dict(self, obj):
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict(include_related=False)
        return {'id': obj.id, 'name': str(obj)} if obj else None

    def to_dict(self, include_related: bool = True) -> dict:
        data = {
            'id': self.id,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'reason': self.reason,
            'issued_at': self.issued_at.isoformat(),
            'is_active': self.end_date > timezone.now()  # Add an active status helper
        }
        if include_related:
            data['user'] = self._get_related_object_dict(self.user)
            data['space_type'] = self._get_related_object_dict(self.space_type)
            data['ban_policy_applied'] = self._get_related_object_dict(self.ban_policy_applied)
            data['issued_by'] = self._get_related_object_dict(self.issued_by)
        return data


# ====================================================================
# UserSpaceTypeExemption Model (用户空间类型豁免 - 白名单)
# ====================================================================
class UserSpaceTypeExemption(models.Model):
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
        permissions = (
            ("can_view_user_exemptions", "Can view user exemption records"),
            ("can_manage_user_exemptions", "Can create, edit, delete user exemption records"),
        )
        indexes = [
            Index(fields=['user']),
            Index(fields=['space_type']),
            Index(fields=['start_date', 'end_date']),
        ]

    def __str__(self):
        space_type_name = self.space_type.name if self.space_type else "全局"
        return f"{self.user.get_full_name} 豁免 {space_type_name}"

    def _get_related_object_dict(self, obj):
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict(include_related=False)
        return {'id': obj.id, 'name': str(obj)} if obj else None

    def to_dict(self, include_related: bool = True) -> dict:
        data = {
            'id': self.id,
            'exemption_reason': self.exemption_reason,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'granted_at': self.granted_at.isoformat(),
            'is_active': (self.start_date is None or self.start_date <= timezone.now()) and \
                         (self.end_date is None or self.end_date > timezone.now())
        }
        if include_related:
            data['user'] = self._get_related_object_dict(self.user)
            data['space_type'] = self._get_related_object_dict(self.space_type)
            data['granted_by'] = self._get_related_object_dict(self.granted_by)
        return data


# ====================================================================
# DailyBookingLimit Model (每日预订限制)
# ====================================================================
class DailyBookingLimit(models.Model):
    """
    定义不同用户组和/或空间类型的每日最大预订次数限制。
    """
    objects = models.Manager()

    group = models.ForeignKey(  # 从 OneToOneField 改为 ForeignKey
        Group,
        on_delete=models.CASCADE,
        related_name='daily_booking_limits',  # 改为 plural
        verbose_name="用户组",
        help_text="此每日预订限制规则应用的用户组。"
    )
    space_type = models.ForeignKey(  # 新增 space_type 字段
        SpaceType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='daily_booking_limits',  # 改为 plural
        verbose_name="限制应用空间类型",
        help_text="此每日预订限制规则应用的空间类型；为空则表示该组的全局限制"
    )
    max_bookings = models.PositiveIntegerField(
        default=0,  # 0表示没有限制
        verbose_name="每日最大预订次数",
        help_text="该组用户每天最多可以进行的预订次数。设置为0表示没有限制。"
    )
    priority = models.PositiveIntegerField(  # 新增 priority 字段
        default=0,
        verbose_name="策略优先级",
        help_text="当多个策略（如全局和特定空间类型）满足条件时，数字越大优先级越高（0为最低）"
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
        # 确保每个用户组对于每个空间类型（或全局）只有一个活跃限制
        unique_together = ('group', 'space_type')
        ordering = ['group__name', '-priority']  # 默认按优先级排序
        permissions = [
            ("can_view_daily_booking_limits", "Can view daily booking limits"),
            ("can_manage_daily_booking_limits", "Can manage daily booking limits (add, change, delete)"),
        ]
        indexes = [
            Index(fields=['group']),
            Index(fields=['space_type']),  # 新增索引
            Index(fields=['is_active']),
            Index(fields=['priority']),  # 新增索引
        ]

    def __str__(self):
        limit_str = f"{self.max_bookings} 次" if self.max_bookings > 0 else "无限制"
        space_type_name = self.space_type.name if self.space_type else "全局"
        return f"{self.group.name} 在 {space_type_name} 下的每日预订限制: {limit_str} (优先级:{self.priority}, {'启用' if self.is_active else '禁用'})"

    def _get_related_object_dict(self, obj):
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict(include_related=False)
        # Assuming Group has an id and name. If not, adjust.
        return {'id': obj.id, 'name': str(obj.name)} if obj else None

    def to_dict(self, include_related: bool = True) -> dict:
        data = {
            'id': self.id,
            'max_bookings': self.max_bookings,
            'priority': self.priority,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }
        if include_related:
            data['group'] = self._get_related_object_dict(self.group)
            data['space_type'] = self._get_related_object_dict(self.space_type)
        return data