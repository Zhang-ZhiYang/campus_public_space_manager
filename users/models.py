from django.contrib.auth.models import AbstractUser
from django.db import models

# ====================================================================
# 新增：Role 模型
# ====================================================================
class Role(models.Model):
    """
    用户角色模型，定义了不同用户的权限组。
    例如：学生、空间管理员、系统管理员等。
    """
    name = models.CharField(max_length=50, unique=True, verbose_name="角色名称")
    description = models.TextField(blank=True, verbose_name="角色描述")

    class Meta:
        verbose_name = '角色'
        verbose_name_plural = verbose_name
        ordering = ['name'] # 按名称排序显示

    def __str__(self):
        return self.name

ROLE_STUDENT = '学生'
ROLE_SPACE_MANAGER = '空间管理员'
ROLE_ADMIN = '系统管理员'
ROLE_SUPERUSER = '超级管理员'

# ====================================================================
# 修正：CustomUser 模型，添加 role 字段
# ====================================================================
class CustomUser(AbstractUser):
    """
    自定义用户模型，继承自 Django 标准的 AbstractUser。
    添加了项目需要的额外用户字段，包括学号、专业、班级、性别、违约次数和用户角色。
    """

    GENDER_CHOICES = (
        ('M', '男'),
        ('F', '女'),
        ('U', '未知'),
    )

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

    student_id = models.CharField(
        max_length=20,
        unique=True,
        blank=False,
        null=False,
        verbose_name="学号",
        help_text="学生唯一的学号"
    )
    major = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="专业",
        help_text="用户所属的专业"
    )
    student_class = models.CharField(
        max_length=50,
        blank=True,
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

    # 外键关联到 Role 模型
    # on_delete=models.SET_NULL 意味着如果角色被删除，用户的 role 字段会变为 NULL。
    # blank=True, null=True 允许用户在创建时没有明确指定角色，或角色暂时为空。
    # 建议在创建用户时给一个默认角色，例如 '学生'。
    role = models.ForeignKey(
        'Role',
        on_delete=models.SET_NULL,
        null=True,      # 允许为空
        blank=True,     # 允许表单为空
        related_name='users', # 反向查询时，Role 对象可以通过 .users 访问所有关联用户
        verbose_name="用户角色",
        help_text="用户在系统中的权限角色"
    )

    class Meta:
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.username} ({self.student_id})"

    # 便捷方法：检查用户是否为某个角色
    @property
    def is_student(self):
        return self.role and self.role.name == ROLE_STUDENT

    @property
    def is_space_manager(self):
        return self.role and self.role.name == ROLE_SPACE_MANAGER

    @property
    def is_admin(self):
        # 系统管理员一般会同时是 is_staff=True，具体看你的权限设计
        return self.role and self.role.name == ROLE_ADMIN

    @property
    def is_super_admin(self):
        # 通常超级管理员拥有最高权限，且 is_superuser=True
        return self.is_superuser # 直接使用 Django 自带的 is_superuser