# users/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager, Group # 导入 Group
from django.db.models import Manager
from django.db.models.signals import post_save, pre_delete # 引入Django信号
from django.dispatch import receiver # 引入Django信号

# ====================================================================
# 角色常量 (保持不变)
# ====================================================================
ROLE_STUDENT = '学生'
ROLE_SPACE_MANAGER = '空间管理员'
ROLE_ADMIN = '系统管理员'
ROLE_SUPERUSER = '超级管理员' # 用于Group命名，与is_superuser对应

GENDER_CHOICES = (
    ('M', '男'),
    ('F', '女'),
    ('U', '未知'),
)

# ====================================================================
# Role Model (角色) - 保持不变
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
# CustomUser Model (自定义用户) - 核心修改在save方法和signals
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

    full_name = models.CharField(max_length=255, blank=True, null=True, verbose_name="姓名")
    phone_number = models.CharField(max_length=15, blank=True, null=True, unique=True, verbose_name="手机号")
    work_id = models.CharField(max_length=20, unique=True, verbose_name="工号/学号", help_text="系统内唯一标识")
    major = models.CharField(max_length=100, blank=True, null=True, verbose_name="专业")
    student_class = models.CharField(max_length=50, blank=True, null=True, verbose_name="班级")
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default='U', blank=True, verbose_name="性别")
    total_violation_count = models.IntegerField(default=0, verbose_name="累计违约次数")

    role = models.ForeignKey( # 保持ForeignKey连接到Role
        Role,
        on_delete=models.SET_NULL, # 当角色被删除时，用户角色设为NULL
        null=True,
        blank=True,
        related_name='users',
        verbose_name="用户角色"
    )

    email = models.EmailField(
        blank=True, null=True, unique=True, verbose_name="电子邮件", default=None
    )

    class Meta(AbstractUser.Meta):
        verbose_name = '用户'
        verbose_name_plural = verbose_name
        swappable = 'AUTH_USER_MODEL'

    # `@property` 辅助方法可以保留，以供业务逻辑或API使用，但在Admin中，我们将直接使用Django权限
    @property
    def is_super_admin(self):
        return self.is_superuser

    @property
    def is_admin(self):
        return self.is_super_admin or \
               (self.role and self.role.name == ROLE_ADMIN)

    @property
    def is_space_manager(self):
        return self.is_admin or \
               (self.role and self.role.name == ROLE_SPACE_MANAGER)

    @property
    def is_student(self):
        return self.role and self.role.name == ROLE_STUDENT

    def save(self, *args, **kwargs):
        if self.email == '':
            self.email = None

        # 任何有管理权限的角色都应该能登录 Admin (is_staff=True)
        # 这确保了具有任何管理角色的用户，即使没有is_superuser，也能登录Admin界面
        should_be_staff = self.is_super_admin or self.is_admin or self.is_space_manager
        if self.is_staff != should_be_staff:
            self.is_staff = should_be_staff

        super().save(*args, **kwargs)

    def __str__(self):
        return self.username

# ====================================================================
# Django Signal：在用户保存后，将其同步到对应的 Group
# ====================================================================
@receiver(post_save, sender=CustomUser)
def sync_user_to_groups_based_on_role(sender, instance, created, **kwargs):
    # 此信号用于将用户根据其主要角色分配到Django Groups
    # 目的：利用Django Groups来集中管理权限，同时保持Role的语义

    # 定义所有可能成为“主要角色组”的名称
    all_role_group_names = {ROLE_STUDENT, ROLE_SPACE_MANAGER, ROLE_ADMIN, ROLE_SUPERUSER}

    # 获取用户当前被分配到的所有角色组
    user_current_role_groups = instance.groups.filter(name__in=all_role_group_names)

    # 确定用户理论上应该属于的单一角色组
    target_group_name = None
    if instance.is_superuser:
        target_group_name = ROLE_SUPERUSER
    elif instance.role:
        target_group_name = instance.role.name

    target_group = None
    if target_group_name and target_group_name in all_role_group_names:
        target_group, _ = Group.objects.get_or_create(name=target_group_name)

    # 移除用户不再应该属于的角色组
    # 这是关键：只移除与 `all_role_group_names` 对应的组，不影响管理员可能手动添加的其他功能组
    for group in user_current_role_groups:
        if group != target_group:
            instance.groups.remove(group)

    # 将用户添加到它应该属于的角色组
    if target_group and target_group not in user_current_role_groups:
        instance.groups.add(target_group)

    # 如果用户没有角色（role为None），且非superadmin，则移除所有角色组
    if not instance.role and not instance.is_superuser and user_current_role_groups.exists():
        instance.groups.remove(*user_current_role_groups)

@receiver(pre_delete, sender=Role)
def remove_users_from_deleted_role_group(sender, instance, **kwargs):
    # 在 Role 对象被删除之前，将所有与该Role相关的用户从对应的Django Group中移除
    # 这可以在某些情况下避免用户属于一个不再代表实际角色的Group
    try:
        group = Group.objects.get(name=instance.name)
        # 找到所有拥有此 Role 的用户，并从该 Group 中移除
        # instance.users 是 CustomUser 中 role 字段的 related_name
        for user in instance.users.all():
            user.groups.remove(group)
    except Group.DoesNotExist:
        pass # 对应的Group可能不存在，忽略