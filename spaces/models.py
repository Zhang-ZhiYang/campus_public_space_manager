# spaces/models.py
from django.db import models
from django.db.models import Manager, Index # 导入 Manager, Index
from datetime import timedelta, time
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Group
from django.conf import settings
from django.dispatch import receiver
from guardian.shortcuts import assign_perm, remove_perm
from django.db.models.signals import post_save, pre_save

# CRITICAL FIX: 移除 setup_perm_query_set 的导入，以及所有自定义 PermManager 和 PermQuerySet 的定义

# 获取 CustomUser 模型
CustomUser = settings.AUTH_USER_MODEL

# ====================================================================
# SpaceType Model (空间类型)
# ====================================================================
class SpaceType(models.Model):
    """
    定义空间的类型，例如：教学楼、教室、会议室、实验室等。
    添加默认预订规则和基础设施标识。
    """
    # CRITICAL FIX: 使用默认的 models.Manager
    objects = models.Manager()

    name = models.CharField(max_length=100, unique=True, verbose_name="空间类型名称")
    description = models.TextField(blank=True, verbose_name="类型描述")

    is_container_type = models.BooleanField(
        default=False,
        verbose_name="是否为容器类型",
        help_text="如教学楼，通常不直接预订，但包含子空间或设施"
    )
    is_basic_infrastructure = models.BooleanField(
        default=False,
        verbose_name="是否为基础型基础设施",
        help_text="如果为True，该类型空间/设施通常可由普通用户（如学生）预订，无需特定对象级权限。"
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
        indexes = [
            Index(fields=['name']),
            Index(fields=['is_container_type']),
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
    # CRITICAL FIX: 使用默认的 models.Manager
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
            ("can_manage_bookable_amenity", "Can manage this specific bookable amenity"),
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

    restricted_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name='restricted_spaces',
        verbose_name="受限用户组",
        help_text="属于这些用户组的用户不能预订此空间。"
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
            ("can_book_this_space", "Can book this specific space"),
            ("can_book_amenities_in_space", "Can book amenities within this space"), # <--- 新增权限
            ("can_manage_space_details", "Can manage details of this specific space"),
            ("can_manage_space_bookings", "Can manage bookings of this specific space"),
            ("can_manage_space_amenities", "Can manage amenities of this specific space"),
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

        if self.pk and self.parent_space == self:
            raise ValidationError({'parent_space': '空间不能将自身设置为父级空间。'})

        if self.parent_space and self.parent_space.pk:
            current = self.parent_space
            while current:
                if current == self:
                    raise ValidationError({'parent_space': '父级空间不能是其子空间或孙子空间。'})
                current = current.parent_space

    def save(self, *args, **kwargs):
        if not self.is_active:
            self.is_bookable = False

        if self.is_container:
            self.is_bookable = False

        if self.space_type:
            if self.space_type.is_container_type:
                self.is_container = True
                self.is_bookable = False # 容器类型通常不可预订

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

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.location})"

# ====================================================================
# Django Signals for Space and BookableAmenity
# ====================================================================

@receiver(pre_save, sender=Space)
def store_old_managed_by_for_space(sender, instance, **kwargs):
    """在保存前存储旧的 managed_by 用户，以便 post_save 判断其是否改变，并移除旧权限。"""
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk) # Using the model's default manager
            instance._old_managed_by = old_instance.managed_by
        except sender.DoesNotExist:
            instance._old_managed_by = None
    else:
        instance._old_managed_by = None

@receiver(post_save, sender=Space)
def assign_space_management_permissions(sender, instance, created, **kwargs):
    """
    当 Space 对象创建或更新时，为 managed_by 用户分配对象级管理权限。
    并处理 managed_by 变更时旧用户的权限移除。
    """
    permissions = [
        'can_manage_space_details',
        'can_manage_space_bookings',
        'can_manage_space_amenities',
        'can_book_this_space',
        'can_book_amenities_in_space', # <--- 管理员也应该有这个权限
    ]

    old_managed_by = getattr(instance, '_old_managed_by', None)
    if old_managed_by and old_managed_by != instance.managed_by:
        for perm in permissions:
            remove_perm(f'spaces.{perm}', old_managed_by, instance)

    if instance.managed_by:
        space_manager_group = Group.objects.filter(name='空间管理员').first()
        if space_manager_group and instance.managed_by not in space_manager_group.customuser_set.all():
            instance.managed_by.groups.add(space_manager_group)

        for perm in permissions:
            assign_perm(f'spaces.{perm}', instance.managed_by, instance)

@receiver(post_save, sender=BookableAmenity)
def assign_amenity_management_permissions(sender, instance, created, **kwargs):
    """
    当 BookableAmenity 对象创建或更新时，如果其所属 Space 有管理人员，
    则为其分配该 BookableAmenity 的管理权限。
    """
    if instance.space and instance.space.managed_by:
        manager = instance.space.managed_by
        # 这个权限是管理“设施实例”的，与预订设施的权限不同
        assign_perm('spaces.can_manage_bookable_amenity', manager, instance)