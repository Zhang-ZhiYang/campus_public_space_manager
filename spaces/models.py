# spaces/models.py
from django.db import models
from django.db.models import Manager
from datetime import timedelta, time
from django.core.exceptions import ValidationError
from django.utils import timezone  # 导入 timezone for default time

# 导入 Role 模型，确保它在 users 应用中定义且可用
try:
    from users.models import Role
except ImportError:
    class Role(models.Model):
        name = models.CharField(max_length=50, unique=True)

        def __str__(self): return self.name


    print("Warning: users.models.Role could not be imported. Using a mock Role for spaces/models.py. "
          "Ensure 'users' app is in INSTALLED_APPS and Role model is correctly defined.")


# ====================================================================
# SpaceType Model (空间类型) - (保持不变)
# ====================================================================
class SpaceType(models.Model):
    objects: Manager = Manager()

    name = models.CharField(max_length=100, unique=True, verbose_name="空间类型名称")
    description = models.TextField(blank=True, verbose_name="类型描述")

    is_container_type = models.BooleanField(default=False, verbose_name="是否为容器类型")
    default_is_bookable = models.BooleanField(default=True, verbose_name="默认是否可预订")
    default_requires_approval = models.BooleanField(default=True, verbose_name="默认是否需要审批")
    default_available_start_time = models.TimeField(null=True, blank=True, verbose_name="默认每日最早可预订时间",
                                                    default=time(8, 0))
    default_available_end_time = models.TimeField(null=True, blank=True, verbose_name="默认每日最晚可预订时间",
                                                  default=time(22, 0))
    default_min_booking_duration = models.DurationField(null=True, blank=True, verbose_name="默认单次预订最短时长",
                                                        default=timedelta(minutes=30))
    default_max_booking_duration = models.DurationField(null=True, blank=True, verbose_name="默认单次预订最长时长",
                                                        default=timedelta(hours=4))
    default_buffer_time_minutes = models.PositiveIntegerField(default=0, verbose_name="默认前后预订缓冲时间(分钟)")

    class Meta:
        verbose_name = '空间类型'
        verbose_name_plural = verbose_name
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['is_container_type']),
        ]

    def __str__(self): return self.name


# ====================================================================
# Amenity Model (设施 - 定义设施的种类) - (保持不变)
# ====================================================================
class Amenity(models.Model):
    objects: Manager = Manager()

    name = models.CharField(max_length=100, unique=True, verbose_name="设施名称")
    description = models.TextField(blank=True, verbose_name="设施描述")
    is_bookable_individually = models.BooleanField(default=False, verbose_name="是否可单独预订")

    class Meta:
        verbose_name = '设施类型'
        verbose_name_plural = verbose_name
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['is_bookable_individually']),
        ]

    def __str__(self): return self.name


# ====================================================================
# BookableAmenity Model (可预订设施实例 - Space 下的设施具体数量) - (保持不变)
# ====================================================================
class BookableAmenity(models.Model):
    objects: Manager = Manager()

    space = models.ForeignKey(
        'Space',
        on_delete=models.CASCADE,
        related_name='bookable_amenities',
        verbose_name="所属空间"
    )
    amenity = models.ForeignKey(
        Amenity,
        on_delete=models.CASCADE,
        related_name='bookable_instances',
        verbose_name="设施类型"
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name="总数量")
    is_bookable = models.BooleanField(default=True, verbose_name="是否可预订")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")

    class Meta:
        verbose_name = '空间设施实例'
        verbose_name_plural = verbose_name
        unique_together = ('space', 'amenity')
        ordering = ['space__name', 'amenity__name']
        indexes = [
            models.Index(fields=['space', 'amenity']),
            models.Index(fields=['is_bookable']),
            models.Index(fields=['is_active']),
        ]

    def clean(self):
        super().clean()
        if self.amenity and not self.amenity.is_bookable_individually and self.is_bookable:
            raise ValidationError(
                {'is_bookable': f"设施类型 '{self.amenity.name}' 不可单独预订，不能设置此实例为可预订。"})
        if not self.is_active and self.is_bookable:
            raise ValidationError({'is_bookable': '不活跃的设施实例不能设置为可预订。'})
        if self.quantity <= 0:
            raise ValidationError({'quantity': '设施数量必须大于0。'})

    def save(self, *args, **kwargs):
        if self.amenity:
            if not self.amenity.is_bookable_individually:
                self.is_bookable = False
        if not self.is_active:
            self.is_bookable = False
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        activity_status = "活跃" if self.is_active else "不活跃"
        return f"{self.space.name} 的 {self.amenity.name} (数量: {self.quantity}, 状态: {activity_status})"


# ====================================================================
# Space Model (空间) - 核心修改在这里
# ====================================================================
class Space(models.Model):
    """
    可预订空间模型，定义了每个空间的属性和预订规则。
    新增基于角色的预订访问控制（黑名单策略）。
    """
    objects: Manager = Manager()

    name = models.CharField(max_length=255, unique=True, verbose_name="空间名称")
    location = models.CharField(max_length=255, verbose_name="位置信息", help_text="例如：B座301室")
    description = models.TextField(blank=True, verbose_name="详细描述", help_text="空间的详细介绍和使用注意事项")
    capacity = models.PositiveIntegerField(default=1, verbose_name="容量", help_text="可容纳人数")

    parent_space = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='child_spaces',
        verbose_name="父级空间",
        help_text="该空间所属的父级空间（如：教学楼下的教室）"
    )
    space_type = models.ForeignKey(
        SpaceType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='spaces',
        verbose_name="空间类型"
    )
    is_container = models.BooleanField(
        default=False,
        verbose_name="是否为容器空间",
        help_text="如果为True，通常表示该空间仅作为父级组织其他子空间或设施，不能直接预订。"
    )

    is_bookable = models.BooleanField(default=True, verbose_name="是否可预订", help_text="空间是否对外开放预订")
    is_active = models.BooleanField(default=True, verbose_name="是否启用", help_text="空间是否处于启用状态")
    image = models.ImageField(upload_to='space_images/', blank=True, null=True, verbose_name="空间图片")

    requires_approval = models.BooleanField(
        default=True,
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

    # --- 新增字段：基于角色的预订访问控制 (黑名单策略) ---
    restricted_roles = models.ManyToManyField(
        Role,
        blank=True,
        related_name='restricted_spaces',
        verbose_name="禁止预订的角色",
        help_text="在此列表中的角色将无法预订此空间。如果列表为空，则所有角色均可预订。"
    )
    # --- 新增字段结束 ---

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = '空间'
        verbose_name_plural = verbose_name
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['location']),
            models.Index(fields=['space_type']),
            models.Index(fields=['parent_space']),
            models.Index(fields=['is_bookable']),
            models.Index(fields=['is_active']),
            models.Index(fields=['is_container']),
            models.Index(fields=['requires_approval']),
            models.Index(fields=['created_at']),
        ]

    def clean(self):
        super().clean()

        # 现有业务规则
        if not self.is_active and self.is_bookable:
            raise ValidationError({'is_bookable': '不活跃的空间不能设置为可预订。'})
        if self.available_start_time and self.available_end_time and \
                self.available_start_time >= self.available_end_time:  # 修正为 >=
            raise ValidationError({'available_end_time': '每日最晚可预订时间必须晚于最早可预订时间。'})
        if self.is_container and self.is_bookable:
            raise ValidationError({'is_bookable': '容器空间通常不直接预订，请设置 is_bookable 为 False。'})

        # 避免自关联的循环引用 (A是B的父，B是A的父)
        if self.pk and self.parent_space == self:
            raise ValidationError({'parent_space': '空间不能将自身设置为父级空间。'})

        # 业务规则4: 避免父级空间是其子空间或孙子空间 (更健壮的循环检测)
        if self.parent_space and self.parent_space.pk:
            current = self.parent_space
            path = {current.pk}
            while current.parent_space:
                current = current.parent_space
                if current.pk == self.pk:  # 直接检测到循环
                    raise ValidationError({'parent_space': '父级空间不能是其子空间或孙子空间。'})
                if current.pk in path:  # 检测到更长的循环
                    raise ValidationError({'parent_space': '父级空间链中存在循环。'})
                path.add(current.pk)

    def save(self, *args, **kwargs):
        """
        重写 save 方法，在保存时：
        1. 根据 is_active 和 is_container 强制设置 is_bookable 状态。
        2. 根据 space_type 自动填充尚未设置的默认预订规则。
        """
        # 逻辑1: 强制设置 is_bookable
        if not self.is_active:
            self.is_bookable = False  # 如果空间不活跃，强制设置为不可预订

        if self.is_container:  # 如果是容器空间，强制设置为不可预订
            self.is_bookable = False

        # 逻辑2: 根据 space_type 自动填充默认值 (仅在字段未设置时)
        if self.space_type:
            # 如果空间类型是容器类型，强制设置 is_container (尽管 clean 已经有校验了)
            if self.space_type.is_container_type:
                self.is_container = True
                self.is_bookable = False  # 容器类型通常不可预订

            if self.requires_approval is None:
                self.requires_approval = self.space_type.default_requires_approval
            if self.available_start_time is None:
                self.available_start_time = self.space_type.default_available_start_time
            if self.available_end_time is None:
                self.available_end_time = self.space_type.default_available_end_time
            if self.min_booking_duration is None:
                self.min_booking_duration = self.space_type.default_min_booking_duration
            if self.max_booking_duration is None:
                self.max_booking_duration = self.space_type.default_max_booking_duration
            if self.buffer_time_minutes is None:
                self.buffer_time_minutes = self.space_type.default_buffer_time_minutes

        self.full_clean()  # 触发 clean() 方法进行模型验证以及字段验证
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.location})"

    # --- 新增辅助方法：用于检查某个角色是否可以预订此空间 ---
    def can_role_book(self, user_role: 'Role') -> bool:
        """
        根据 spaces/models.py 中的 Space.restricted_roles 字段
        检查给定用户的角色是否可以预订此空间。

        Args:
            user_role: CustomUser 实例的 role 字段 (Role 模型实例)。

        Returns:
            bool: 如果该用户角色可以预订此空间，则返回 True，否则返回 False。
        """
        if not user_role or not isinstance(user_role, Role):
            # 用户没有角色，或者传递的不是有效的 Role 实例
            return False

        # 如果 restricted_roles 列表为空，表示没有角色被限制，所有角色都可以预订。
        # 考虑到 ManyToManyField 在未保存前可能无法访问，这里用 .exists() 会更安全。
        # self.restricted_roles 是 ManyToManyManager
        if not self.restricted_roles.exists():  # 检查是否有任何限制角色存在
            return True

        # 如果 restricted_roles 列表不为空，则检查用户的角色是否在其中。
        # 如果在列表中，表示该角色被限制，不能预订。
        if self.restricted_roles.filter(pk=user_role.pk).exists():  # 更高效地检查是否存在
            return False

        # 如果 restricted_roles 不为空，且用户角色不在其中，则可以预订。
        return True