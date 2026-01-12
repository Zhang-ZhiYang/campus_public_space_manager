# users/models.py (修订版)
from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager, Group, Permission
from django.db.models import Index
from guardian.shortcuts import get_perms_for_model
from django.contrib.contenttypes.models import ContentType  # 导入 ContentType
import logging

logger = logging.getLogger(__name__)

# ====================================================================
# 性别选择项
# ====================================================================
GENDER_CHOICES = (
    ('M', '男'),
    ('F', '女'),
    ('U', '未知'),
)


# ====================================================================
# CustomUser Model (自定义用户)
# ====================================================================
class CustomUserManager(UserManager):
    def create_user(self, username, email=None, password=None, **extra_fields):
        if email == '':
            email = None
        return super().create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        if email == '':
            email = None
        return super().create_superuser(username, email, password, **extra_fields)


class CustomUser(AbstractUser):
    objects = CustomUserManager()

    name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="姓名",
        help_text="用户的真实姓名"
    )

    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        unique=True,
        verbose_name="手机号",
        help_text="用户的手机号码，唯一"
    )
    work_id = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        null=True,  # 允许为空，但如果非空则唯一
        verbose_name="工号/学号",
        help_text="系统内唯一标识，如学号、工号"
    )
    major = models.CharField(
        max_length=100,
        blank=True,
        null=True,  # 允许为空
        verbose_name="专业",
        help_text="学生用户的专业信息"
    )
    student_class = models.CharField(
        max_length=50,
        blank=True,
        null=True,  # 允许为空
        verbose_name="班级",
        help_text="学生用户的班级信息"
    )
    gender = models.CharField(
        max_length=1,
        choices=GENDER_CHOICES,
        default='U',
        null=True,
        blank=True,  # 允许为空
        verbose_name="性别"
    )

    email = models.EmailField(
        blank=True,
        null=True,
        unique=True,
        verbose_name="电子邮件",
        default=None
    )

    groups = models.ManyToManyField(
        Group,
        verbose_name='groups',
        blank=True,
        help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.',
        related_name="customuser_set",
        related_query_name="customuser",
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name='user permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        related_name="customuser_set",
        related_query_name="customuser",
    )

    class Meta(AbstractUser.Meta):
        verbose_name = '用户'
        verbose_name_plural = verbose_name
        swappable = 'AUTH_USER_MODEL'
        indexes = [
            Index(fields=['phone_number']),
            Index(fields=['work_id']),
            Index(fields=['email']),
            Index(fields=['name']),
        ]

    @property
    def is_system_admin(self):
        """
        判断用户是否为系统管理员。系统管理员拥有最高级别的管理权限。
        系统管理员可以由 is_superuser 或属于 '系统管理员' 组来标识。
        """
        return self.is_superuser or self.groups.filter(name='系统管理员').exists()

    @property
    def is_space_manager(self):
        """
        判断用户是否为空间管理员。空间管理员管理特定空间及相关业务。
        包括系统管理员，因为系统管理员可以做所有空间管理员能做的事情。
        """
        return self.is_system_admin or self.groups.filter(name='空间管理员').exists()

    @property
    def is_teacher(self):
        """是否是教师（通过属于'教师'组判断）"""
        return self.groups.filter(name='教师').exists()

    @property
    def is_student(self):
        """是否是学生（通过属于'学生'组判断）"""
        return self.groups.filter(name='学生').exists()

    @property
    def is_staff_member(self):
        """
        通用的“员工”或“内部人员”标识，可以用于所有管理类角色的基准。
        例如，is_system_admin 或 is_space_manager 都属于 staff_member。
        """
        return self.is_system_admin or self.is_space_manager

    def get_all_group_permissions(self):
        """
        获取用户通过其所在组拥有的所有权限 (字符串形式)。
        如果用户是超级用户，则返回所有模型权限。
        """
        if self.is_superuser:
            return set(f"{perm.content_type.app_label}.{perm.codename}" for perm in Permission.objects.all())
        return set(self.get_group_permissions())

    def get_all_object_permissions(self, obj):
        """
        获取用户对特定对象拥有的所有对象级权限 (字符串形式)。
        如果用户是超级用户，则默认对所有对象拥有所有权限 (概念上，不需要额外查询逐一检查)。
        """
        if self.is_superuser:
            # Superuser implicitly has all permissions. This is a conceptual bypass.
            # To get actual codenames for a model, query permissions for that model's ContentType
            content_type = ContentType.objects.get_for_model(obj.__class__)
            return set([f"{content_type.app_label}.{perm.codename}" for perm in
                        Permission.objects.filter(content_type=content_type)])
        return set([f"{g_perm.content_type.app_label}.{g_perm.codename}" for g_perm in get_perms_for_model(self, obj)])

    @property
    def get_full_name(self):
        """
        获取用户的完整姓名。
        优先使用 name 字段，其次是 first_name 和 last_name，最后是 username。
        """
        if self.name:
            return self.name
        elif self.first_name and self.last_name:
            first = self.first_name if self.first_name else ''
            last = self.last_name if self.last_name else ''
            full_name = f"{first} {last}".strip()
            return full_name if full_name else self.username
        elif self.first_name:
            return self.first_name
        elif self.last_name:
            return self.last_name
        return self.username

    def save(self, *args, **kwargs):
        if self.email == '':
            self.email = None

        is_new_user = not self.pk

        # 在调用 super().save() 之前确保 is_staff 字段的更新不会导致递归
        # 正确的做法是使用 post_save 信号或明确 update_fields
        # 这里的逻辑是确保 is_staff 在用户创建或组关系变化时更新
        if self.pk:  # Only process if user already exists in DB
            old_is_staff = CustomUser.objects.filter(pk=self.pk).values_list('is_staff', flat=True).first()
            if old_is_staff is None:  # Should not happen if self.pk exists, but for safety
                old_is_staff = self.is_staff  # Fallback to current instance value

        super().save(*args, **kwargs)  # Call original save

        # 确保只在用户对象已经存在（有主键）时才执行组相关的 is_staff 逻辑
        if self.pk:
            current_is_staff_status = self.is_staff  # Load current value fresh from DB AFTER base save

            # Check for changes in group membership or is_superuser status that would affect is_staff
            should_be_staff = self.is_superuser or \
                              self.groups.filter(name='系统管理员').exists() or \
                              self.groups.filter(name='空间管理员').exists()

            if current_is_staff_status != should_be_staff:
                self.is_staff = should_be_staff
                # Only save the 'is_staff' field to prevent recursion
                super().save(update_fields=['is_staff'])

    def __str__(self):
        return self.get_full_name