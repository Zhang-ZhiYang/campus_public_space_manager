from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Manager
from django.utils import timezone # 导入 timezone，用于 token_version 等日期字段

# ====================================================================
# Role 模型
# ====================================================================
class Role(models.Model):
    objects: Manager = Manager()

    name = models.CharField(max_length=50, unique=True, verbose_name="角色名称")
    description = models.TextField(blank=True, verbose_name="角色描述")

    class Meta:
        verbose_name = '角色'
        verbose_name_plural = verbose_name
        ordering = ['name']

    def __str__(self):
        return self.name

ROLE_STUDENT = '学生'
ROLE_SPACE_MANAGER = '空间管理员'
ROLE_ADMIN = '系统管理员'
ROLE_SUPERUSER = '超级管理员'

# ====================================================================
# CustomUser 模型
# ====================================================================
class CustomUser(AbstractUser):
    GENDER_CHOICES = (
        ('M', '男'),
        ('F', '女'),
        ('U', '未知'),
    )

    # === 新增：full_name 字段 ===
    full_name = models.CharField(
        max_length=255,
        blank=True,  # 允许在表单/请求中为空字符串
        null=True,   # 允许数据库存储 NULL
        verbose_name="姓名",
        help_text="用户的完整姓名"
    )
    # ==========================

    total_violation_count = models.IntegerField(
        default=0,
        verbose_name="总违约次数",
        help_text="用户在所有空间累计的总违约次数"
    )
    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        unique=True,
        verbose_name="手机号"
    )

    work_id = models.CharField(
        max_length=20,
        unique=True,
        blank=False,
        null=False,
        verbose_name="工号/学号",
        help_text="用户在系统中的唯一工号或学号"
    )

    major = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="专业",
        help_text="用户所属的专业"
    )
    student_class = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="班级",
        help_text="用户所属的班级"
    )
    gender = models.CharField(
        max_length=1,
        choices=GENDER_CHOICES,
        default='U',
        blank=True,
        verbose_name="性别"
    )

    role = models.ForeignKey(
        'Role',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        verbose_name="用户角色",
        help_text="用户在系统中的权限角色"
    )


    class Meta:
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        # 更新 __str__ 方法，优先显示 full_name。如果为空，则显示 username 和 work_id
        return self.full_name or f"{self.username} ({self.work_id})"

    @property
    def is_student(self):
        return self.role and self.role.name == ROLE_STUDENT

    @property
    def is_space_manager(self):
        return self.role and self.role.name == ROLE_SPACE_MANAGER

    @property
    def is_admin(self):
        return self.role and self.role.name == ROLE_ADMIN

    @property
    def is_super_admin(self):
        return self.is_superuser