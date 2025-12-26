from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """
    自定义用户模型，继承自 Django 标准的 AbstractUser。
    这里添加所有项目需要的额外用户字段。
    """

    # 比如我们之前设计的：记录总违约次数
    total_violation_count = models.IntegerField(
        default=0,
        verbose_name="总违约次数",
        help_text="用户在所有空间累计的总违约次数"
    )

    # 手机号 (通常是必填项)
    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        verbose_name="手机号"
    )

    # 你可以在这里继续添加 role 等字段，或者暂时保持简单
    # role = models.ForeignKey('Role', ...)

    class Meta:
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.username