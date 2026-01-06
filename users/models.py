# users/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager, Group # 移除了 AnonymousUser，它不属于 models.py
from django.db.models import Index

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
        if email == '': # Django 默认会将空字符串视为 NULL 或空，这里统一处理
            email = None
        return super().create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        if email == '': # 同上，统一处理
            email = None
        return super().create_superuser(username, email, password, **extra_fields)

class CustomUser(AbstractUser):
    objects = CustomUserManager()

    # 新增 name 字段
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
        null=True, # 允许为空
        verbose_name="专业",
        help_text="学生用户的专业信息"
    )
    student_class = models.CharField(
        max_length=50,
        blank=True,
        null=True, # 允许为空
        verbose_name="班级",
        help_text="学生用户的班级信息"
    )
    gender = models.CharField(
        max_length=1,
        choices=GENDER_CHOICES,
        default='U',
        blank=True, # 允许为空
        verbose_name="性别"
    )

    email = models.EmailField(
        blank=True,
        null=True,
        unique=True,
        verbose_name="电子邮件",
        default=None
    )
    # 添加 related_name 以避免和 auth.User 组字段冲突 (如果需要)
    groups = models.ManyToManyField(
        Group,
        verbose_name='groups',
        blank=True,
        help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.',
        related_name="customuser_set", # <--- 添加这里
        related_query_name="customuser",
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name='user permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        related_name="customuser_set", # <--- 添加这里
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
            Index(fields=['name']),  # 为新字段添加索引
        ]

    @property
    def is_system_admin(self):
        """是否是系统管理员（通过is_superuser或属于'系统管理员'组判断）"""
        # 注意：这里假设 CustomUser 实例有 groups 属性
        # is_superuser 总是比组检查优先
        return self.is_superuser or self.groups.filter(name='系统管理员').exists()

    @property
    def is_space_manager(self):
        """是否是空间管理员（包括系统管理员权限）"""
        # 优化：如果已经是系统管理员，就已经是空间管理员
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
        """
        获取用户的完整姓名。
        优先使用 name 字段，其次是 first_name 和 last_name，最后是 username。
        """
        if self.name:
            return self.name
        elif self.first_name and self.last_name:
            # 确保即使有一个为空也能正确拼接
            first = self.first_name if self.first_name else ''
            last = self.last_name if self.last_name else ''
            full_name = f"{first} {last}".strip()
            return full_name if full_name else self.username
        elif self.first_name:
            return self.first_name
        elif self.last_name:
            return self.last_name
        return self.username  # fallback to username if no name parts

    def save(self, *args, **kwargs):
        # 处理 email 字段的空字符串，统一设置为 None
        if self.email == '':
            self.email = None

        # 首次保存 CustomUser 对象，使其获得 id (Primary Key)
        # 这对于处理 ManyToMany 字段（如 groups）是必要的，因为 M2M 关系需要在保存对象后才能建立或修改
        super_save_result = super().save(*args, **kwargs)

        # 确保只在用户对象已经存在（有主键）时才执行组相关的 is_staff 逻辑
        if self.pk:
            # 刷新对象以确保 self.groups 缓存是最新的，特别是当组在 Admin 中被修改时
            # M2M 字段的保存通常发生在父对象保存之后，所以刷新很重要
            self.refresh_from_db()

            # 判断当前用户是否应该具有 staff 权限
            # 1. 如果是超级用户，始终是 staff
            # 2. 如果属于 '系统管理员' 组，是 staff
            # 3. 如果属于 '空间管理员' 组，是 staff
            should_be_staff = self.is_superuser or \
                              self.groups.filter(name='系统管理员').exists() or \
                              self.groups.filter(name='空间管理员').exists()

            # 如果 is_staff 的当前值与期望值不符，则更新它
            if self.is_staff != should_be_staff:
                self.is_staff = should_be_staff
                # 仅保存 is_staff 字段，以避免再次触发整个 save 循环 (无限递归)
                super().save(update_fields=['is_staff'])

        return super_save_result # 返回 super().save() 的结果

    def __str__(self):
        return self.get_full_name