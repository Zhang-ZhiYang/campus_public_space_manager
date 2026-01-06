# users/apps.py
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'
    verbose_name = "用户管理"

    def ready(self):
        # ⚠️ 将 AnonymousUser 的导入移动到 ready() 方法内部
        # 这样可以确保在导入它时，Django 的应用注册表已经完全加载完毕
        from django.contrib.auth.models import AnonymousUser

        # 动态地给 AnonymousUser 类添加自定义属性
        # 这些属性在未登录时应始终返回 False 或一个安全默认值
        # 这样，任何尝试访问 AnonymousUser.is_system_admin 的代码都不会报错

        if not hasattr(AnonymousUser, 'is_system_admin'):
            @property
            def _is_system_admin(self):
                return False

            AnonymousUser.is_system_admin = _is_system_admin

        if not hasattr(AnonymousUser, 'is_space_manager'):
            @property
            def _is_space_manager(self):
                return False

            AnonymousUser.is_space_manager = _is_space_manager

        if not hasattr(AnonymousUser, 'is_teacher'):
            @property
            def _is_teacher(self):
                return False

            AnonymousUser.is_teacher = _is_teacher

        if not hasattr(AnonymousUser, 'is_student'):
            @property
            def _is_student(self):
                return False

            AnonymousUser.is_student = _is_student

        # 确保 AnonymousUser 有一个 get_full_name 属性
        if not hasattr(AnonymousUser, 'get_full_name'):
            @property
            def _anonymous_get_full_name(self):
                return _("匿名用户")

            AnonymousUser.get_full_name = _anonymous_get_full_name
