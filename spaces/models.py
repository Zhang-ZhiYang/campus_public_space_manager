# spaces/models.py
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Manager, Index, Q
from datetime import timedelta, time
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Group
from django.conf import settings
import logging
from guardian.shortcuts import assign_perm, remove_perm # NEW: 导入 assign_perm, remove_perm

# 获取 CustomUser 模型
CustomUser = get_user_model()

logger = logging.getLogger(__name__)

# ====================================================================
# 通用权限定义 (放在模块级别，方便复用和管理)
# ====================================================================

# 空间管理员拥有的管理权限 (对空间对象及其子孙)
SPACE_MANAGEMENT_PERMISSIONS = [
    'can_view_space',
    'can_edit_space_info',
    'can_change_space_status',
    'can_configure_booking_rules',
    'can_manage_permitted_groups',
    'can_add_space_amenity',  # 管理设施内联时使用
    'can_view_space_bookings',
    'can_approve_space_bookings',
    'can_checkin_space_bookings',
    'can_cancel_space_bookings',
    'can_book_this_space',
    'can_book_amenities_in_space',
    'can_check_in_real_space', # NEW: 添加签到权限
]

# 空间管理员拥有的仅查看权限 (对父级空间)
SPACE_VIEW_ONLY_PERMISSIONS = ['can_view_space']

# 空间管理员拥有的对 BookableAmenity 的管理权限
BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS = [
    'can_view_bookable_amenity',
    'can_edit_bookable_amenity_quantity',
    'can_change_bookable_amenity_status',
]

# 签到方法选择项 <--- NEW: 增加了 LOCATION 选项
CHECK_IN_METHOD_CHOICES = (
    ('NONE', '不需要签到'),
    ('SELF', '用户自行签到'),
    ('STAFF', '仅限签到员签到'),
    ('HYBRID', '用户和签到员均可签到'),
    ('LOCATION', '仅限定位签到'), # <--- 新增
)

CHECK_IN_METHOD_NONE = 'NONE'
CHECK_IN_METHOD_SELF = 'SELF'
CHECK_IN_METHOD_STAFF = 'STAFF'
CHECK_IN_METHOD_HYBRID = 'HYBRID'
CHECK_IN_METHOD_LOCATION = 'LOCATION' # <--- 新增常量

# ====================================================================
# 辅助函数：获取空间的所有后代 (子孙) 空间
# ====================================================================
def get_all_descendant_spaces(space_instance):
    """
    通过迭代方式获取给定空间的所有后代（子孙）空间。
    使用 BFS 避免深度递归栈溢出，并检测循环引用。
    """
    descendants = set()
    queue = list(space_instance.child_spaces.all())
    seen_pks = set()

    for child in queue:
        if child.pk not in seen_pks:
            seen_pks.add(child.pk)
        else:
            logger.warning(
                f"Duplicate direct child {child.name} (PK:{child.pk}) detected for Space {space_instance.name} during initial descendant lookup.")

    current_level = queue
    next_level = []

    while current_level:
        for current_child in current_level:
            descendants.add(current_child)

            for deeper_child in current_child.child_spaces.all():
                if deeper_child.pk not in seen_pks:
                    seen_pks.add(deeper_child.pk)
                else:
                    logger.warning(
                        f"Circular reference or duplicate child {deeper_child.name} (PK:{deeper_child.pk}) detected during descendant lookup from parent {current_child.name} (root: {space_instance.name}). Skipping.")

        current_level = next_level
        next_level = []

    return descendants

# ====================================================================
# SpaceType Model (空间类型)
# ====================================================================
class SpaceType(models.Model):
    """
    定义空间的类型，例如：教学楼、教室、会议室、实验室等。
    添加默认预订规则和基础设施标识。
    """
    objects = models.Manager()

    name = models.CharField(max_length=100, unique=True, verbose_name="空间类型名称")
    description = models.TextField(blank=True, verbose_name="类型描述")

    is_basic_infrastructure = models.BooleanField(
        default=False,
        verbose_name="是否为基础型基础设施",
        help_text="如果为True，该类型空间/设施通常可由所有认证用户预订/访问，无需特定对象级权限。"
    )

    # 默认预订规则字段，作为创建 Space 时的初始模板
    default_is_bookable = models.BooleanField(default=True, verbose_name="默认是否可预订")
    default_requires_approval = models.BooleanField(
        default=False,
        verbose_name="默认是否需要审批",
        help_text="新创建的空间默认是否需要管理员审核批准"
    )
    # 新增：默认签到方式，包含 LOCATION 选项
    default_check_in_method = models.CharField(
        max_length=10,
        choices=CHECK_IN_METHOD_CHOICES,
        default=CHECK_IN_METHOD_HYBRID,
        verbose_name="默认签到方式",
        help_text="该类型空间默认的签到方式，可被具体空间覆盖。"
    )
    default_available_start_time = models.TimeField(
        null=True, blank=True,
        verbose_name="默认每日最早可预订时间",
        default=time(8, 0)
    )
    default_available_end_time = models.TimeField(
        null=True, blank=True,
        verbose_name="默认每日最晚可预订时间",
        default=time(22, 0)
    )
    default_min_booking_duration = models.DurationField(
        null=True, blank=True,
        verbose_name="默认单次预订最短时长",
        default=timedelta(minutes=30)
    )
    default_max_booking_duration = models.DurationField(
        null=True, blank=True,
        verbose_name="默认单次预订最长时长",
        default=timedelta(hours=4)
    )
    default_buffer_time_minutes = models.PositiveIntegerField(
        default=0,
        verbose_name="默认前后预订缓冲时间(分钟)",
        help_text="相邻预订之间的最短间隔（分钟）"
    )

    class Meta:
        verbose_name = '空间类型'
        verbose_name_plural = verbose_name
        ordering = ['name']
        permissions = (
            ("can_view_spacetype", "Can view space types"),
            ("can_create_spacetype", "Can create space types"),
            ("can_edit_spacetype", "Can edit space types"),
            ("can_delete_spacetype", "Can delete space types"),
        )
        indexes = [
            Index(fields=['name']),
            Index(fields=['is_basic_infrastructure']),
        ]

    def __str__(self):
        return self.name

    def to_dict(self):
        """
        将 SpaceType 实例转换为字典，准备用于缓存。
        DurationField 不再转换为字符串，而是保持 timedelta 类型。
        """
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'is_basic_infrastructure': self.is_basic_infrastructure,
            'default_is_bookable': self.default_is_bookable,
            'default_check_in_method': self.default_check_in_method,  # <--- 包含新字段
            'default_requires_approval': self.default_requires_approval,
            'default_available_start_time': self.default_available_start_time.strftime(
                '%H:%M:%S') if self.default_available_start_time else None,
            'default_available_end_time': self.default_available_end_time.strftime(
                '%H:%M:%S') if self.default_available_end_time else None,
            'default_min_booking_duration': self.default_min_booking_duration,
            'default_max_booking_duration': self.default_max_booking_duration,
            'default_buffer_time_minutes': self.default_buffer_time_minutes,
        }

# ====================================================================
# Amenity Model (设施 - 定义设施的种类)
# ====================================================================
class Amenity(models.Model):
    """
    设施种类模型，例如投影仪、白板、Wi-Fi、椅子等。
    它定义了设施的类型，不关心具体数量和所属空间。
    """
    objects: Manager = models.Manager()

    name = models.CharField(max_length=100, unique=True, verbose_name="设施名称")
    description = models.TextField(blank=True, verbose_name="设施描述")
    is_bookable_individually = models.BooleanField(
        default=False,
        verbose_name="是否可单独预订",
        help_text="如果为True，则可创建为单独的预订目标；否则只能作为空间的一部分提供"
    )

    class Meta:
        verbose_name = '设施类型'
        verbose_name_plural = verbose_name
        ordering = ['name']
        permissions = (
            ("can_view_amenity", "Can view amenity types"),
            ("can_create_amenity", "Can create amenity types"),
            ("can_edit_amenity", "Can edit amenity types"),
            ("can_delete_amenity", "Can delete amenity types"),
        )
        indexes = [
            Index(fields=['name']),
            Index(fields=['is_bookable_individually']),
        ]

    def __str__(self):
        return self.name

    def to_dict(self):
        """
        将 Amenity 实例转换为字典，准备用于缓存。
        """
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'is_bookable_individually': self.is_bookable_individually,
        }

# ====================================================================
# BookableAmenity Model (可预订设施实例 - Space 下的设施具体数量)
# ====================================================================
class BookableAmenity(models.Model):
    """
    特定空间中可预订的设施实例。
    例如：A教室有 2 个“投影仪”实例，B会议室有 10 把“椅子”实例。
    """
    objects = models.Manager()

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
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name="总数量",
        help_text="该空间中此类型设施的总数"
    )
    is_bookable = models.BooleanField(
        default=True,
        verbose_name="是否可预订",
        help_text="此设施实例在该空间中是否对外开放预订"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="是否启用",
        help_text="此设施实例是否处于启用状态"
    )

    class Meta:
        verbose_name = '空间设施实例'
        verbose_name_plural = verbose_name
        unique_together = ('space', 'amenity')
        ordering = ['space__name', 'amenity__name']
        indexes = [
            Index(fields=['space', 'amenity']),
            Index(fields=['is_bookable']),
            Index(fields=['is_active']),
        ]
        permissions = (
            ("can_view_bookable_amenity", "Can view this bookable amenity instance"),
            ("can_edit_bookable_amenity_quantity", "Can edit quantity of this bookable amenity instance"),
            ("can_change_bookable_amenity_status", "Can change active/bookable status of this instance"),
            ("can_delete_bookable_amenity", "Can delete this bookable amenity instance"),
        )

    def clean(self):
        super().clean()

        if self.amenity and not self.amenity.is_bookable_individually and self.is_bookable:
            raise ValidationError(
                {'is_bookable': f"设施类型 '{self.amenity.name}' 不可单独预订，不能设置此实例为可预订。"}
            )

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

    def to_dict(self, include_related=True):
        """
        将 BookableAmenity 实例转换为字典，准备用于缓存。
        会嵌套其关联的 Amenity。
        """
        data = {
            'id': self.id,
            'space_id': self.space_id,
            'quantity': self.quantity,
            'is_bookable': self.is_bookable,
            'is_active': self.is_active,
        }
        if include_related and self.amenity: # 根据 include_related 参数控制是否嵌套 amenity
            data['amenity'] = self.amenity.to_dict() # Amenity.to_dict() 应该不需要 include_related
        return data

class Space(models.Model):
    """
    可预订空间模型，定义了每个空间的属性和预订规则。
    """
    objects = models.Manager()

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

    # NEW: 添加地理坐标字段
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name="纬度",
        help_text="空间地理坐标的纬度，例如：30.287"
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name="经度",
        help_text="空间地理坐标的经度，例如：120.124"
    )

    requires_approval = models.BooleanField(
        default=False,
        verbose_name="需要管理员审批",
        help_text="预订此空间是否需要管理员审核批准"
    )

    # 签到方式字段，允许为空以继承 SpaceType 的默认值
    check_in_method = models.CharField(
        max_length=10,
        choices=CHECK_IN_METHOD_CHOICES,
        null=True, blank=True,  # 允许为空，表示继承 SpaceType 的默认值
        verbose_name="签到方式",
        help_text="该空间的签到方式。若为空，则继承空间类型的默认值。若为'不需要签到'，则无需用户或签到员操作。"
    )

    # NEW: 添加 check_in_by 字段
    check_in_by = models.ManyToManyField(
        CustomUser,
        blank=True,
        related_name='can_check_in_spaces',
        verbose_name="可签到人员",
        help_text="有权为该空间签到的用户列表，通常是'签到员'角色"
    )

    available_start_time = models.TimeField(
        null=True, blank=True, verbose_name="每日最早可预订时间", help_text="例如 08:00"
    )
    available_end_time = models.TimeField(
        null=True, blank=True, verbose_name="每日最晚可预订时间", help_text="例如 22:00"
    )

    min_booking_duration = models.DurationField(
        null=True, blank=True,
        verbose_name="单次预订最短时长",
        help_text="例如 30 分钟。为空则继承 SpaceType 默认或不限制"
    )
    max_booking_duration = models.DurationField(
        null=True, blank=True,
        verbose_name="单次预订最长时长",
        help_text="例如 4 小时。为空则继承 SpaceType 默认或不限制"
    )
    buffer_time_minutes = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name="前后预订缓冲时间(分钟)",
        help_text="相邻预订之间的最短间隔（分钟）。为空则继承 SpaceType 默认或无"
    )

    permitted_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name='permitted_spaces',
        verbose_name="可预订用户组",
        help_text="如果空间非基础型基础设施，则只有属于这些用户组的用户才能预订/访问此空间。若为空，则除管理员、空间经理和基础型之外，该空间对非管理员用户不可访问。"
    )

    managed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='managed_spaces',
        verbose_name="主要管理人员",
        help_text="该空间的主要管理人员，通常是空间管理员"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    @property
    def effective_check_in_method(self):
        """
        计算并返回此空间的有效签到方式。
        优先使用空间自身设置，其次是空间类型默认设置，最后是兜底默认值。
        """
        if self.check_in_method:
            return self.check_in_method
        elif self.space_type and self.space_type.default_check_in_method:
            return self.space_type.default_check_in_method
        return CHECK_IN_METHOD_HYBRID # 兜底默认值，确保总返回一个有效值
    class Meta:
        verbose_name = '空间'
        verbose_name_plural = verbose_name
        ordering = ['name']
        permissions = (
            ("can_view_space", "Can view this specific space details"),
            ("can_create_space", "Can create a new space"),
            ("can_edit_space_info", "Can edit basic information of this space"),
            ("can_change_space_status", "Can change active/bookable status of this space"),
            ("can_configure_booking_rules", "Can configure specific booking rules for this space"),
            ("can_assign_space_manager", "Can assign/change manager of this space"),
            ("can_manage_permitted_groups", "Can manage permitted groups for this space"),
            ("can_delete_space", "Can delete this space"),
            ("can_add_space_amenity", "Can add amenity to this space"),
            ("can_view_space_bookings", "Can view bookings for this specific space"),
            ("can_approve_space_bookings", "Can approve/reject bookings for this specific space"),
            ("can_checkin_space_bookings", "Can check-in/out bookings for this specific space"),
            ("can_cancel_space_bookings", "Can cancel bookings for this specific space"),
            ("can_book_this_space", "Can book this specific space"),
            ("can_book_amenities_in_space", "Can book amenities within this space"),
            ("can_check_in_real_space", "Can check-in bookings for this specific space (real-time field)"), # NEW: 新增权限
        )
        indexes = [
            Index(fields=['name']),
            Index(fields=['location']),
            Index(fields=['space_type']),
            Index(fields=['parent_space']),
            Index(fields=['is_bookable']),
            Index(fields=['is_active']),
            Index(fields=['is_container']),
            Index(fields=['requires_approval']),
            Index(fields=['managed_by']),
            Index(fields=['created_at']),
            Index(fields=['check_in_method']),
            Index(fields=['latitude', 'longitude']), # <-- 新增索引
        ]

    def clean(self):
        super().clean()

        if not self.is_active and self.is_bookable:
            raise ValidationError({'is_bookable': '不活跃的空间不能设置为可预订。'})

        if self.available_start_time and self.available_end_time and \
                self.available_start_time >= self.available_end_time:
            raise ValidationError({'available_end_time': '每日最晚可预订时间必须晚于最早可预订时间。'})

        if self.is_container and self.is_bookable:
            raise ValidationError({'is_bookable': '容器空间通常不直接预订，请设置 is_bookable 为 False。'})

        if self.space_type and not self.space_type.default_is_bookable and self.is_bookable:
            raise ValidationError(
                {'is_bookable': f"所属空间类型 '{self.space_type.name}' 默认不可预订，此空间不能设置为可预订。"},
                code='space_type_not_bookable_conflict'
            )

        if self.pk and self.parent_space == self:
            raise ValidationError({'parent_space': '空间不能将自身设置为父级空间。'})

        # 循环引用检测 (保持不变)
        if self.parent_space and self.pk:
            current = self.parent_space
            processed_pks = {self.pk}
            traversal_path = [self.name]

            while current:
                if current.pk in processed_pks:
                    path_str = " -> ".join(traversal_path + [current.name])
                    raise ValidationError(
                        {'parent_space': f'父级空间不能是其子空间或孙子空间（检测到循环引用: {path_str}）。'})

                processed_pks.add(current.pk)
                traversal_path.append(current.name)
                current = current.parent_space

    def save(self, *args, **kwargs):

        if not self.is_active:
            self.is_bookable = False

        if self.is_container:
            self.is_bookable = False

        if self.space_type:
            # 继承 SpaceType 的默认规则 (仅当 Space 自己的字段为空时)
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
            if self.check_in_method is None or self.check_in_method == '':
                self.check_in_method = self.space_type.default_check_in_method

            # 如果空间类型默认不可预订，则此空间也不可预订
            if not self.space_type.default_is_bookable:
                self.is_bookable = False
        else:
            if self.check_in_method is None or self.check_in_method == '':
                self.check_in_method = CHECK_IN_METHOD_HYBRID

        self.full_clean()
        super().save(*args, **kwargs)


    def __str__(self):
        return f"{self.name} ({self.location})"

    def to_dict(self, include_related=True):
        """
        将 Space 实例转换为字典，准备用于缓存。
        **新增 latitude, longitude 字段，并确保 DurationField 保持 timedelta 类型。**
        """
        data = {
            'id': self.pk,
            'name': self.name,
            'location': self.location,
            'description': self.description,
            'capacity': self.capacity,
            'parent_space_id': self.parent_space_id,
            'space_type_id': self.space_type_id,
            'is_container': self.is_container,
            'is_bookable': self.is_bookable,
            'is_active': self.is_active,
            'image': self.image.url if self.image else None,
            'latitude': float(self.latitude) if self.latitude is not None else None, # <--- 包含新字段
            'longitude': float(self.longitude) if self.longitude is not None else None, # <--- 包含新字段
            'requires_approval': self.requires_approval,  # 模型字段值
            'check_in_method': self.check_in_method,
            'available_start_time': self.available_start_time.strftime(
                '%H:%M:%S') if self.available_start_time else None,
            'available_end_time': self.available_end_time.strftime('%H:%M:%S') if self.available_end_time else None,
            'min_booking_duration': self.min_booking_duration,
            'max_booking_duration': self.max_booking_duration,
            'buffer_time_minutes': self.buffer_time_minutes,
            'managed_by_id': self.managed_by_id,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }
        if include_related:
            # 嵌套关联对象 (如果需要，也要确保它们返回 timedelta 对象)
            data['space_type'] = self.space_type.to_dict() if self.space_type else None
            data['managed_by'] = self.managed_by.to_dict_minimal() if self.managed_by else None
            data['bookable_amenities'] = [ba.to_dict() for ba in self.bookable_amenities.all()]
            data['permitted_groups'] = [group.pk for group in self.permitted_groups.all()]
            # NEW: 添加 check_in_by 用户ID列表
            data['check_in_by'] = [user.pk for user in self.check_in_by.all()]

            # 计算并添加有效的预订规则字段
            data['effective_requires_approval'] = self.requires_approval if self.requires_approval is not None else (
                self.space_type.default_requires_approval if self.space_type else False)
            data['effective_available_start_time'] = (self.available_start_time or (
                self.space_type.default_available_start_time if self.space_type else None)).strftime('%H:%M:%S') if (
                    self.available_start_time or (
                self.space_type.default_available_start_time if self.space_type else None)) else None
            data['effective_available_end_time'] = (self.available_end_time or (
                self.space_type.default_available_end_time if self.space_type else None)).strftime('%H:%M:%S') if (
                    self.available_end_time or (
                self.space_type.default_available_end_time if self.space_type else None)) else None

            data['effective_min_booking_duration'] = self.min_booking_duration or (
                self.space_type.default_min_booking_duration if self.space_type else None)
            data['effective_max_booking_duration'] = self.max_booking_duration or (
                self.space_type.default_max_booking_duration if self.space_type else None)

            data[
                'effective_buffer_time_minutes'] = self.buffer_time_minutes if self.buffer_time_minutes is not None else (
                self.space_type.default_buffer_time_minutes if self.space_type else 0)
            data['permitted_groups_display'] = ", ".join(
                [group.name for group in self.permitted_groups.all()]) if self.permitted_groups.exists() else (
                "所有人" if self.space_type and self.space_type.is_basic_infrastructure else "无特定限制 (需权限)")

            # 计算并添加有效的签到方式
            data['effective_check_in_method'] = self.check_in_method if self.check_in_method is not None else (
                self.space_type.default_check_in_method if self.space_type else CHECK_IN_METHOD_HYBRID
            )
            # 添加签到方式的显示名称，方便前端展示
            data['effective_check_in_method_display'] = dict(CHECK_IN_METHOD_CHOICES).get(
                data['effective_check_in_method'], '未知')

        return data