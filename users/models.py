from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """
    自定义用户模型，继承自 Django 标准的 AbstractUser。
    添加了项目需要的额外用户字段，包括学号、专业、班级、性别和违约次数。
    """

    # 性别选项
    GENDER_CHOICES = (
        ('M', '男'),
        ('F', '女'),
        ('U', '未知'),  # 可选，如果用户不愿透露
    )

    # 之前已有的字段
    total_violation_count = models.IntegerField(
        default=0,
        verbose_name="总违约次数",
        help_text="用户在所有空间累计的总违约次数"
    )
    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        unique=True,  # 手机号通常也需要唯一
        verbose_name="手机号"
    )

    # 新增的字段
    student_id = models.CharField(
        max_length=20,
        unique=True,  # 学号必须是唯一的
        blank=False,  # 学号一般是必填
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
        default='U',  # 默认未知
        blank=True,  # 允许为空
        verbose_name="性别"
    )

    class Meta:
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.username} ({self.student_id})"