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

    # ================================================================
    # 新增辅助方法，根据 role 字段判断用户权限
    # ================================================================
    @property
    def is_student(self):
        return self.role and self.role.name == ROLE_STUDENT

    @property
    def is_space_manager(self):
        return self.is_superuser or (self.role and self.role.name == ROLE_SPACE_MANAGER)

    @property
    def is_admin(self):  # 系统管理员
        return self.is_superuser or (self.role and self.role.name == ROLE_ADMIN)

    @property
    def is_super_admin(self):  # 超级管理员
        return self.is_superuser

    # ================================================================
    # 关键修改: 重写 CustomUser 的 save 方法，在保存前清理空字符串为 None
    # ================================================================
    def save(self, *args, **kwargs):
        if self.email == '':
            self.email = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.username