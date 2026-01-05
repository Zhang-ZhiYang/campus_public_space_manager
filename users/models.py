# users/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager, Group  # 确保导入 Group
from django.db.models import Manager, Index
from datetime import timedelta

# 请注意：如果你在其他地方（如signals.py）也引用了CustomUser，请确保它最新的定义被正确导入

# ====================================================================
# 性别选择项
# ====================================================================
GENDER_CHOICES = (
    ('M', '男'),
    ('F', '女'),
    ('U', '未知'),
)


# ====================================================================
# CustomUser Model (自定义用户) - 移除了 UserSpaceTypeExemption 引用
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
        null=True,
        verbose_name="专业",
        help_text="学生用户的专业信息"
    )
    student_class = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="班级",
        help_text="学生用户的班级信息"
    )
    gender = models.CharField(
        max_length=1,
        choices=GENDER_CHOICES,
        default='U',
        blank=True,
        verbose_name="性别"
    )
    # total_penalty_points_ever 字段已移除，因为可以通过聚合查询获得

    email = models.EmailField(
        blank=True,
        null=True,
        unique=True,
        verbose_name="电子邮件",
        default=None  # 显式为 None
    )

    class Meta(AbstractUser.Meta):
        verbose_name = '用户'
        verbose_name_plural = verbose_name
        swappable = 'AUTH_USER_MODEL'
        indexes = [
            Index(fields=['phone_number']),  # 常用查询
            Index(fields=['work_id']),  # 常用查询
            Index(fields=['email']),  # 常用查询
        ]

    # @property 方法现在直接检查 Group 成员关系
    @property
    def is_system_admin(self):
        """是否是系统管理员（通过is_superuser或属于'系统管理员'组判断）"""
        return self.is_superuser or self.groups.filter(name='系统管理员').exists()

    @property
    def is_space_manager(self):
        """是否是空间管理员（包括系统管理员权限）"""
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
    def get_full_name(self):
        """获取用户的完整姓名，优先使用first_name和last_name，否则使用username"""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        elif self.first_name:
            return self.first_name
        elif self.last_name:
            return self.last_name
        return self.username  # fallback to username if no name parts

    def save(self, *args, **kwargs):
        # 处理 email 为空字符串的情况，确保存储为 None
        if self.email == '':
            self.email = None

        # 首次保存 CustomUser 对象，使其获得 id
        is_new_user = self.pk is None  # 判断是否为新用户
        super().save(*args, **kwargs)  # ⚠️ 将保存操作提前，此时 self.pk 就有值了

        # 获取 Group 对象（如果它们存在）
        # 确保 Groups 已经存在，如果不存在，首次运行脚本或创建时会自动创建。
        # 这里用try-except防止Group不存在导致错误
        try:
            admin_group = Group.objects.get(name='系统管理员')
        except Group.DoesNotExist:
            admin_group = None

        try:
            spacemanager_group = Group.objects.get(name='空间管理员')
        except Group.DoesNotExist:
            spacemanager_group = None

        # 现在 self.pk 已存在，可以安全地查询 self.groups
        should_be_staff = self.is_superuser
        if admin_group:
            should_be_staff = should_be_staff or self.groups.filter(pk=admin_group.pk).exists()
        if spacemanager_group:
            should_be_staff = should_be_staff or self.groups.filter(pk=spacemanager_group.pk).exists()

        # 检查是否需要更新 is_staff
        if self.is_staff != should_be_staff:
            self.is_staff = should_be_staff
            # 注意：这里需要再次调用 save，但要避免无限循环
            # 可以通过 update_fields 或传递一个标记来避免
            # 更好的方法是在首次 save 后，直接在这里更新 is_staff，并只保存这个字段
            super().save(update_fields=['is_staff'])  # 只更新 is_staff 字段

    def __str__(self):
        return self.get_full_name

# ====================================================================
# UserSpaceTypeExemption Model 已被移动到 bookings/models.py
# ====================================================================