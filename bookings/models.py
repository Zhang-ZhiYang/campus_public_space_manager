# bookings/models.py
from io import BytesIO

import qrcode
from django.conf import settings
from django.contrib.auth.models import Group  # 导入 Group 模型
from django.core.files.base import ContentFile
from django.db import models
from django.db.models import Manager, Index  # 导入 Manager, Index
# 修正 datetime 导入：从标准库中导入 datetime 和 timedelta
from datetime import datetime, timedelta, date, time  # 新增 datetime, date, time
from django.core.exceptions import ValidationError
from django.utils import timezone
import uuid  # 导入 uuid 模块
import logging # 导入 logging

# 导入外部模型
from users.models import CustomUser
from spaces.models import Space, BookableAmenity, SpaceType

# 确保能导入 CheckInRecord。如果存在循环导入问题，可以考虑动态导入或调整应用结构
try:
    from check_in.models import CheckInRecord
except ImportError:
    CheckInRecord = None # 如果导入失败，则无法在 to_dict 中引用 CheckInRecord
    logging.warning("Could not import CheckInRecord in bookings/models.py. Booking.to_dict will not include check_in_record data.")

logger = logging.getLogger(__name__) # 初始化 logger

# ====================================================================
# Global Choices Definitions (这些元组可在文件内被模型直接引用或从其他文件导入)
# ====================================================================

# Booking 状态选择项
BOOKING_STATUS_CHOICES_TUPLE = (
    ('PENDING', '待审核'),
    ('APPROVED', '已批准'),
    ('REJECTED', '已拒绝'),
    ('CANCELLED', '已取消'),
    ('COMPLETED', '已完成'),
    ('NO_SHOW', '未到场'),
    ('CHECKED_IN', '已签到'),
    # ('CHECKED_OUT', '已签出'),
)

# 新增预订处理状态，用于异步流程
PROCESSING_STATUS_CHOICES_TUPLE = (
    ('SUBMITTED', '已提交'),  # 初始状态，等待异步处理
    ('IN_PROGRESS', '处理中'),  # 异步任务正在执行深度校验
    ('CREATED', '已创建'),  # 深度校验通过，Booking 记录已创建/更新为最终状态（PENDING/APPROVED）
    ('FAILED_VALIDATION', '校验失败'),  # 深度校验未通过
    ('FAILED_RUNTIME', '运行时错误'),  # 异步任务中发生未知错误
)

# Violation 类型选择项 (从 Violation 类内部移到全局作用域)
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

    # ====== 预订状态常量 (使用字符串字面值，便于直接赋值和比较) ======
    BOOKING_STATUS_PENDING = 'PENDING'
    BOOKING_STATUS_APPROVED = 'APPROVED'
    BOOKING_STATUS_REJECTED = 'REJECTED'
    BOOKING_STATUS_CANCELLED = 'CANCELLED'
    BOOKING_STATUS_COMPLETED = 'COMPLETED'
    BOOKING_STATUS_NO_SHOW = 'NO_SHOW'
    BOOKING_STATUS_CHECKED_IN = 'CHECKED_IN'
    BOOKING_STATUS_CHECKED_OUT = 'CHECKED_OUT'

    # Booking 状态的 choices 元组，现在引用全局定义的元组
    BOOKING_STATUS_CHOICES = BOOKING_STATUS_CHOICES_TUPLE

    # ====== 预订处理状态常量 ======
    PROCESSING_STATUS_SUBMITTED = 'SUBMITTED'
    PROCESSING_STATUS_IN_PROGRESS = 'IN_PROGRESS'
    PROCESSING_STATUS_CREATED = 'CREATED'
    PROCESSING_STATUS_FAILED_VALIDATION = 'FAILED_VALIDATION'
    PROCESSING_STATUS_FAILED_RUNTIME = 'FAILED_RUNTIME'

    # Processing Status 的 choices 元组，现在引用全局定义的元组
    PROCESSING_STATUS_CHOICES = PROCESSING_STATUS_CHOICES_TUPLE

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
        null=False,
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
    expected_attendees = models.PositiveIntegerField(
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
        default=BOOKING_STATUS_PENDING,  # 使用具名常量
        verbose_name="预订状态",
        help_text="预订的当前业务状态（例如：PENDING, APPROVED, CANCELLED）"
    )

    # 新增 processing_status，记录异步处理流程状态
    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default=PROCESSING_STATUS_SUBMITTED,  # 使用具名常量
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
    check_in_qrcode = models.ImageField(
        upload_to='booking_qrcodes/',
        blank=True,
        null=True,
        verbose_name="签到二维码图片",
        help_text="用于员工扫描签到的二维码图片路径"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = '预订'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
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
            Index(fields=['related_space', 'start_time', 'end_time']),
            Index(fields=['status']),
            Index(fields=['processing_status']),
            Index(fields=['request_uuid']),
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
        super().clean()

        # ... (其他验证逻辑，保持不变) ...
        if not ((self.space is not None) ^ (self.bookable_amenity is not None)):
            raise ValidationError('预订必须且只能指定一个目标：空间或设施实例。')

        if self.space:
            self.related_space = self.space
        elif self.bookable_amenity and self.bookable_amenity.space:
            self.related_space = self.bookable_amenity.space
        else:
            raise ValidationError('无法确定预订目标所属的空间。')

        if self.booked_quantity <= 0:
            raise ValidationError({'booked_quantity': '预订数量必须大于0。'})

        if self.bookable_amenity:
            if self.bookable_amenity.quantity is not None and self.booked_quantity > self.bookable_amenity.quantity:
                raise ValidationError(
                    {'booked_quantity': f"预订数量不能超过设施总数量 {self.bookable_amenity.quantity}。"}
                )
        elif self.space:
            if self.booked_quantity != 1:
                raise ValidationError({'booked_quantity': '预订整个空间时，数量必须为1。'})

        if not self.user:
            raise ValidationError("预订用户不能为空。")

        if self.start_time is None or self.end_time is None:
            raise ValidationError('预订的开始时间和结束时间不能为空。')
        if self.start_time >= self.end_time:
            raise ValidationError({'end_time': '结束时间必须晚于开始时间。'})

        # 【重点修改开始】调整“不能预订过去的时间”的验证逻辑
        # 此验证仅适用于：
        # 1. 新创建的预订 (self.pk is None)，防止创建过去时间的预订。
        # 2. 处于“待审核” (PENDING) 状态且仍在等待最终自动化/人工审批的预订，防止审批者通过已过期的待审核预订。
        # 对于已批准(APPROVED)、已签到(CHECKED_IN)、已完成(COMPLETED)、已取消(CANCELLED)、已拒绝(REJECTED)的预订，
        # 其 start_time 在过去是其自然状态，不应触发此验证。

        is_new_booking = self.pk is None  # 判断是否是新创建的实例 (还没有PK)

        is_pending_and_in_review_process = (
                self.status == Booking.BOOKING_STATUS_PENDING  # 业务状态是待审核
                and self.processing_status in [  # 且处理状态仍在进行中或已创建但尚未最终决定
                    Booking.PROCESSING_STATUS_SUBMITTED,
                    Booking.PROCESSING_STATUS_IN_PROGRESS,
                    Booking.PROCESSING_STATUS_CREATED  # Explicitly include created if it means "waiting for approval"
                ]
        )

        # 组合条件：如果是新创建的预订，**或者** 是待审核且在处理流程中的预订，才进行“时间不能在过去”的校验。
        # 此处更改的目的是确保当我们更新如 CHECKED_IN 到 COMPLETED，或 PENDING 到 REJECTED 的现有预订时，
        # 即使其 start_time 已经过去，也不会因为这个验证而抛出错误。
        if (is_new_booking or is_pending_and_in_review_process) and self.start_time < timezone.now():
            raise ValidationError({'start_time': '不能预订过去的时间。'})

    def save(self, *args, **kwargs):
        self.full_clean()

        # 【修改点 1】修改 `is_new` 的判断逻辑，安全访问 `related_space` 的 `requires_approval`
        # 优先使用 related_space，因为它总是会被填充
        requires_approval_from_related_space = False
        if self.related_space:
            requires_approval_from_related_space = self.related_space.requires_approval

        is_new_and_no_qrcode_and_no_approval = (
                (self.check_in_qrcode is None or self.check_in_qrcode == '') and
                not requires_approval_from_related_space  # 使用 related_space 的 requires_approval
        )

        super().save(*args, **kwargs)  # 保存以确保 self.pk 存在

        # 【修改点 2】修改二维码生成逻辑的触发条件
        # 只有在 is_new_and_no_qrcode_and_no_approval 为 True
        # 或者当前预订状态为 APPROVED 且没有二维码时才尝试生成
        if (is_new_and_no_qrcode_and_no_approval) or \
                ((
                         self.check_in_qrcode is None or self.check_in_qrcode == '') and self.status == Booking.BOOKING_STATUS_APPROVED):
            # 确保 related_space 存在且其 effective_check_in_method 为 'STAFF' 时才自动生成
            if self.related_space and self.related_space.effective_check_in_method == 'STAFF':
                self._generate_check_in_qrcode()

    def _generate_check_in_qrcode(self):
        """
        生成一个包含预订ID的二维码，并将其保存到 ImageField。
        二维码的内容可以是 `settings.BASE_URL/checkin/{booking_id}`
        """
        if not self.pk:
            return  # 只有当预订有 PK 后才能生成二维码

        # 定义二维码内容，例如一个指向签到接口的URL
        # 假设你的前端或签到接口是 `BASE_URL/check_in/{booking_id}`
        # 注意：你需要确保 settings.BASE_URL 存在
        base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')  # 替换为你的服务器基础URL
        qrcode_data = f"{base_url}/api/v1/checkin/staff-scan/{self.pk}/"  # 员工扫描的签到接口

        # 生成二维码图片
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(qrcode_data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        # 将图片保存到 BytesIO 对象中
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        file_name = f'booking_qr_{self.pk}.png'

        # 将 BytesIO 对象包装成 Django 的 ContentFile 并保存到 ImageField
        self.check_in_qrcode.save(file_name, ContentFile(buffer.getvalue()), save=False)
        self.save(update_fields=['check_in_qrcode'])  # 仅更新二维码字段，避免递归 save
    def _get_related_object_dict(self, obj):
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict(include_related=False)
        # 对于 Group 模型，通常没有 to_dict 方法，直接返回其 id 和 name
        if isinstance(obj, Group):
            return {'id': obj.id, 'name': obj.name}
        return {'id': obj.id, 'name': str(obj)} if obj else None

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

            # --- CRITICAL FIX HERE ---
            # 必须访问 .url 属性才能获取图片的 URL 字符串，而不是 ImageFieldFile 对象本身
            'check_in_qrcode': self.check_in_qrcode.url if self.check_in_qrcode else None,
        }
        if include_related:
            data['user'] = self._get_related_object_dict(self.user)
            data['space'] = self._get_related_object_dict(self.space)
            data['bookable_amenity'] = self._get_related_object_dict(self.bookable_amenity)
            data['related_space'] = self._get_related_object_dict(self.related_space)
            data['reviewed_by'] = self._get_related_object_dict(self.reviewed_by)
            if self.reviewed_at: data['reviewed_at'] = self.reviewed_at.isoformat()

            # --- ADDITION FOR FRONTEND PHOTO DISPLAY ---
            # 假设 CheckInRecord 有一个 ForeignKey 到 Booking，related_name='check_in_records'
            # 我们需要获取最新的签到记录并将其转换为字典
            if CheckInRecord: # 确保 CheckInRecord 类已成功导入
                try:
                    # 获取此预订的最新签到记录
                    latest_check_in_record = self.check_in_records.order_by('-check_in_time').first()
                    if latest_check_in_record:
                        # 确保 CheckInRecord 有 to_dict 方法并返回 check_in_image.url
                        data['check_in_record'] = latest_check_in_record.to_dict(include_related=False)
                    else:
                        data['check_in_record'] = None
                except Exception as e:
                    logger.warning(f"Error fetching or serializing check_in_record for Booking {self.pk}: {e}")
                    data['check_in_record'] = None # 发生错误时也返回 None
            else:
                data['check_in_record'] = None # 如果 CheckInRecord 类未导入，则不包含此字段

        return data

# ====================================================================
# Violation Model (违约记录)
# ====================================================================
class Violation(models.Model):
    objects = models.Manager()

    # VIOLATION_TYPE_CHOICES 现在是全局的，无需重复定义在类内部。
    # 新增：定义违约类型常量，以便在代码中通过 Violation.VIOLATION_TYPE_XXX 访问
    VIOLATION_TYPE_NO_SHOW = 'NO_SHOW'
    VIOLATION_TYPE_LATE_CANCELLATION = 'LATE_CANCELLATION'
    VIOLATION_TYPE_MISUSE_SPACE = 'MISUSE_SPACE'
    VIOLATION_TYPE_DAMAGE_PROPERTY = 'DAMAGE_PROPERTY'
    VIOLATION_TYPE_OCCUPY_OVERTIME = 'OCCUPY_OVERTIME'
    VIOLATION_TYPE_OTHER = 'OTHER'
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
        choices=VIOLATION_TYPE_CHOICES,  # 现在引用全局定义的元组
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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间") # NEW: 添加创建时间
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间") # NEW: 添加更新时间

    class Meta:
        verbose_name = '违约记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
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
            Index(fields=['created_at']), # NEW: 添加索引
            Index(fields=['updated_at']), # NEW: 添加索引
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
            'created_at': self.created_at.isoformat(), # NEW:
            'updated_at': self.updated_at.isoformat(), # NEW:
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
            Index(fields=['updated_at']), # NEW: 添加索引
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

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name='daily_booking_limits',
        verbose_name="用户组",
        help_text="此每日预订限制规则应用的用户组。"
    )
    space_type = models.ForeignKey(
        SpaceType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='daily_booking_limits',
        verbose_name="限制应用空间类型",
        help_text="此每日预订限制规则应用的空间类型；为空则表示该组的全局限制"
    )
    max_bookings = models.PositiveIntegerField(
        default=0,
        verbose_name="每日最大预订次数",
        help_text="该组用户每天最多可以进行的预订次数。设置为0表示没有限制。"
    )
    priority = models.PositiveIntegerField(
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
        unique_together = ('group', 'space_type')
        ordering = ['group__name', '-priority']
        permissions = [
            ("can_view_daily_booking_limits", "Can view daily booking limits"),
            ("can_manage_daily_booking_limits", "Can manage daily booking limits (add, change, delete)"),
        ]
        indexes = [
            Index(fields=['group']),
            Index(fields=['space_type']),
            Index(fields=['is_active']),
            Index(fields=['priority']),
        ]

    def __str__(self):
        limit_str = f"{self.max_bookings} 次" if self.max_bookings > 0 else "无限制"
        space_type_name = self.space_type.name if self.space_type else "全局"
        return f"{self.group.name} 在 {space_type_name} 下的每日预订限制: {limit_str} (优先级:{self.priority}, {'启用' if self.is_active else '禁用'})"

    def _get_related_object_dict(self, obj):
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict(include_related=False)
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