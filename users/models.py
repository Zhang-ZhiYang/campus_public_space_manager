# users/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager
from django.db.models import Manager

# ====================================================================
# 角色常量 (建议放在这里，或 core/utils/constants.py)
# ====================================================================
ROLE_STUDENT = '学生'
ROLE_SPACE_MANAGER = '空间管理员'
ROLE_ADMIN = '系统管理员'
ROLE_SUPERUSER = '超级管理员'  # 通常与 is_superuser 对应

GENDER_CHOICES = (
    ('M', '男'),
    ('F', '女'),
    ('U', '未知'),
)


# ====================================================================
# Role Model (角色)
# ====================================================================
class Role(models.Model):
    objects: Manager = Manager()

    name = models.CharField(max_length=50, unique=True, verbose_name="角色名称",
                            help_text="如：学生，空间管理员，系统管理员，超级管理员")
    description = models.TextField(blank=True, verbose_name="角色描述")

    class Meta:
        verbose_name = '角色'
        verbose_name_plural = verbose_name
        ordering = ['name']

    def __str__(self):
        return self.name


# ====================================================================
# CustomUser Model (自定义用户)
# ====================================================================
class CustomUserManager(UserManager):
    """
    自定义的用户管理器，可以添加一些特定于应用程序的查询方法。
    """

    def create_user(self, username, email=None, password=None, **extra_fields):
        # 覆写 create_user 来处理 email=None 的默认逻辑
        # super().create_user 可能会默认 email='' 如果没提供
        if email == '':  # 确保空字符串转为 None
            email = None
        return super().create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        if email == '':  # 确保空字符串转为 None
            email = None
        return super().create_superuser(username, email, password, **extra_fields)


class CustomUser(AbstractUser):
    objects = CustomUserManager()  # 使用自定义的管理器

    full_name = models.CharField(max_length=255, blank=True, null=True, verbose_name="姓名")
    phone_number = models.CharField(max_length=15, blank=True, null=True, unique=True, verbose_name="手机号")
    work_id = models.CharField(max_length=20, unique=True, verbose_name="工号/学号", help_text="系统内唯一标识")
    major = models.CharField(max_length=100, blank=True, null=True, verbose_name="专业")
    student_class = models.CharField(max_length=50, blank=True, null=True, verbose_name="班级")
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default='U', blank=True, verbose_name="性别")
    total_violation_count = models.IntegerField(default=0, verbose_name="累计违约次数")

    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        verbose_name="用户角色"
    )

    email = models.EmailField(
        blank=True,
        null=True,
        unique=True,
        verbose_name="电子邮件",
        default=None  # 再次确认这里有 default=None
    )

    class Meta(AbstractUser.Meta):
        verbose_name = '用户'
        verbose_name_plural = verbose_name
        swappable = 'AUTH_USER_MODEL'

    @property
    def is_super_admin(self):
        # 超级管理员就是is_superuser
        return self.is_superuser

    @property
    def is_admin(self):  # 系统管理员
        # 系统管理员包括超级管理员，以及角色明确为系统管理员的用户
        return self.is_super_admin or \
            (self.role and self.role.name == ROLE_ADMIN)

    @property
    def is_space_manager(self):
        # 空间管理员包括超级管理员、系统管理员，以及角色明确为空间管理员的用户
        # 这样确保了层级关系：is_super_admin -> is_admin -> is_space_manager
        return self.is_super_admin or self.is_admin or \
            (self.role and self.role.name == ROLE_SPACE_MANAGER)

    # 仅作演示，实际可能用不到
    @property
    def is_student(self):
        return self.role and self.role.name == ROLE_STUDENT

    # ================================================================
    # 关键：重写 CustomUser 的 save 方法，同步 is_staff 状态
    # ================================================================
    def save(self, *args, **kwargs):
        if self.email == '':
            self.email = None

        # 任何有管理权限的角色都应该能登录 Admin
        should_be_staff = self.is_super_admin or self.is_admin or self.is_space_manager

        # 只在 is_staff 状态需要改变时才更新，防止不必要的数据库写入
        if self.is_staff != should_be_staff:
            self.is_staff = should_be_staff

        super().save(*args, **kwargs)

    def __str__(self):
        return self.username