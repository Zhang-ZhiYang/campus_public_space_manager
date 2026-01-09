# spaces/models.py (终极修订版 - 包含权限继承逻辑)
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Manager, Index, Q
from datetime import timedelta, time
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Group
from django.conf import settings
from django.dispatch import receiver
from guardian.shortcuts import assign_perm, remove_perm
from django.db.models.signals import post_save, pre_save
import logging

# 获取 CustomUser 模型
CustomUser = settings.AUTH_USER_MODEL

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
    # 'can_assign_space_manager', # 系统管理员专属
    # 'can_delete_space', # 系统管理员专属
]

# 空间管理员拥有的仅查看权限 (对父级空间)
SPACE_VIEW_ONLY_PERMISSIONS = ['can_view_space']

# 空间管理员拥有的对 BookableAmenity 的管理权限
BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS = [
    'can_view_bookable_amenity',
    'can_edit_bookable_amenity_quantity',
    'can_change_bookable_amenity_status',
    # 'can_delete_bookable_amenity', # 危险权限，不自动分配
]


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
    seen_pks = {space_instance.pk}  # 记录已处理的PK，用于避免循环和重复

    current_level = queue
    next_level = []

    while current_level:
        for current_child in current_level:
            if current_child.pk not in seen_pks:
                descendants.add(current_child)
                seen_pks.add(current_child.pk)
                next_level.extend(list(current_child.child_spaces.all()))
            else:
                # 理论上 clean() 方法会阻止循环，但这可以作为运行时防范
                logger.warning(
                    f"Circular reference detected or duplicate child processed for Space {current_child.name} (PK:{current_child.pk}) during descendant lookup from parent {space_instance.name}.")

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

    requires_approval = models.BooleanField(
        default=False,
        verbose_name="需要管理员审批",
        help_text="预订此空间是否需要管理员审核批准"
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

        if self.parent_space and self.parent_space.pk:
            current = self.parent_space
            processed_pks = {self.pk}
            while current:
                if current.pk in processed_pks:
                    raise ValidationError({'parent_space': '父级空间不能是其子空间或孙子空间（检测到循环引用）。'})
                processed_pks.add(current.pk)
                current = current.parent_space

    def save(self, *args, **kwargs):
        if not self.is_active:
            self.is_bookable = False

        if self.is_container:
            self.is_bookable = False  # 容器空间不可预订

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

            if not self.space_type.default_is_bookable:
                self.is_bookable = False

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.location})"


# ====================================================================
# Django Signals for Space and BookableAmenity
# ====================================================================

@receiver(pre_save, sender=Space)
def store_old_managed_by_for_space(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            instance._old_managed_by = old_instance.managed_by
        except sender.DoesNotExist:
            instance._old_managed_by = None
    else:
        instance._old_managed_by = None


@receiver(post_save, sender=Space)
def assign_space_management_permissions(sender, instance, created, **kwargs):
    logger.debug(
        f"Handling post_save for Space {instance.name}, PK={instance.pk}, created: {created}. Manager: {instance.managed_by}")

    old_managed_by = getattr(instance, '_old_managed_by', None)
    current_managed_by = instance.managed_by
    CustomUser = get_user_model()

    # --- 1. 撤销旧管理人员的权限 (仅针对当前 Space 实例及其直接关联的 BookableAmenity) ---
    # 撤销逻辑不向上或向下级联，以避免意外移除其他原因授予的权限
    if old_managed_by and old_managed_by != current_managed_by:
        logger.info(f"Revoking direct permissions for old manager {old_managed_by.username} on space {instance.name}.")
        all_relevant_perms = set(SPACE_MANAGEMENT_PERMISSIONS + SPACE_VIEW_ONLY_PERMISSIONS)  # 撤销所有可能被授予的权限
        for perm_codename in all_relevant_perms:
            try:
                remove_perm(f'spaces.{perm_codename}', old_managed_by, instance)
            except Exception as e:
                logger.warning(
                    f"Failed to revoke 'spaces.{perm_codename}' from {old_managed_by.username} for Space {instance.name}: {e}")

        for ba in instance.bookable_amenities.all():
            for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
                try:
                    remove_perm(f'spaces.{perm_codename}', old_managed_by, ba)
                except Exception as e:
                    logger.warning(
                        f"Failed to revoke 'spaces.{perm_codename}' from {old_managed_by.username} for BookableAmenity {ba.id} in space {instance.name}: {e}")

    # --- 2. 授予新管理人员权限 ---
    if current_managed_by:
        logger.info(f"Assigning permissions for new manager {current_managed_by.username} on space {instance.name}.")

        # 确保新管理人员属于 '空间管理员' 组
        space_manager_group, created_group = Group.objects.get_or_create(name='空间管理员')
        if not current_managed_by.groups.filter(name='空间管理员').exists():
            current_managed_by.groups.add(space_manager_group)
            logger.info(
                f"User {current_managed_by.username} added to '空间管理员' group as they manage space {instance.name}.")

        # --- 2.1 对当前 Space 实例直接授予 "管理" 权限 ---
        for perm_codename in SPACE_MANAGEMENT_PERMISSIONS:
            try:
                assign_perm(f'spaces.{perm_codename}', current_managed_by, instance)
                logger.debug(
                    f"Assigned 'spaces.{perm_codename}' to {current_managed_by.username} for direct Space {instance.name}.")
            except Exception as e:
                logger.error(
                    f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for direct Space {instance.name}: {e}")

        # --- 2.2 对当前 Space 的所有 BookableAmenity 实例授予 "管理" 权限 ---
        for ba in instance.bookable_amenities.all().iterator():
            for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
                try:
                    assign_perm(f'spaces.{perm_codename}', current_managed_by, ba)
                    logger.debug(
                        f"Assigned 'spaces.{perm_codename}' to {current_managed_by.username} for BookableAmenity {ba.id} in space {instance.name}.")
                except Exception as e:
                    logger.error(
                        f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for BookableAmenity {ba.id}: {e}")

        # --- 2.3 自下而上：父级空间，授予 **查看** 权限 (Bottom-up inheritance) ---
        parent_space_traversal = instance.parent_space
        processed_parent_pks = set()
        while parent_space_traversal:
            if parent_space_traversal.pk in processed_parent_pks:
                logger.error(
                    f"Circular parent_space reference detected for Space {instance.name} starting from {parent_space_traversal.name}. Stopping parent permission assignment to prevent infinite loop.")
                break
            processed_parent_pks.add(parent_space_traversal.pk)

            for perm_codename in SPACE_VIEW_ONLY_PERMISSIONS:  # Grant only VIEW permission
                try:
                    assign_perm(f'spaces.{perm_codename}', current_managed_by, parent_space_traversal)
                    logger.debug(
                        f"Assigned 'spaces.{perm_codename}' to {current_managed_by.username} for parent Space {parent_space_traversal.name} (view-only via child {instance.name}).")
                except Exception as e:
                    logger.error(
                        f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for parent Space {parent_space_traversal.name}: {e}")

            parent_space_traversal = parent_space_traversal.parent_space

        # --- 2.4 自上而下：子级空间，授予 **管理** 权限 (Top-down inheritance) ---
        # 查找所有子孙空间并授予管理权限
        descendant_spaces = get_all_descendant_spaces(instance)
        for child_space in descendant_spaces:
            for perm_codename in SPACE_MANAGEMENT_PERMISSIONS:  # Grant full MANAGEMENT permission
                try:
                    assign_perm(f'spaces.{perm_codename}', current_managed_by, child_space)
                    logger.debug(
                        f"Assigned 'spaces.{perm_codename}' to {current_managed_by.username} for child Space {child_space.name} (management via parent {instance.name}).")
                except Exception as e:
                    logger.error(
                        f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for child Space {child_space.name}: {e}")

            # 对子空间的 BookableAmenity 也授予管理权限
            for ba_child in child_space.bookable_amenities.all().iterator():
                for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
                    try:
                        assign_perm(f'spaces.{perm_codename}', current_managed_by, ba_child)
                        logger.debug(
                            f"Assigned 'spaces.{perm_codename}' to {current_managed_by.username} for BookableAmenity {ba_child.id} in child space {child_space.name}.")
                    except Exception as e:
                        logger.error(
                            f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for BookableAmenity {ba_child.id} in child space {child_space.name}: {e}")


@receiver(post_save, sender=BookableAmenity)
def assign_amenity_management_permissions_on_create(sender, instance, created, **kwargs):
    # 当 BookableAmenity 被创建或更新时，如果其所属 Space 有 managed_by，则为其分配对象级权限
    # 这个信号器作为冗余机制，确保即使在 Space 更新导致级联权限分配前，
    # 新创建的 BookableAmenity 也能立即获得权限。
    if instance.space and instance.space.managed_by:
        manager = instance.space.managed_by
        for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
            try:
                assign_perm(f'spaces.{perm_codename}', manager, instance)
                logger.debug(
                    f"Assigned 'spaces.{perm_codename}' to {manager.username} for BookableAmenity {instance.id} in space {instance.space.name}.")
            except Exception as e:
                logger.error(
                    f"Failed to assign 'spaces.{perm_codename}' to {manager.username} for BookableAmenity {instance.id}: {e}")