# core/decorators.py
from functools import wraps
from core.utils.exceptions import ForbiddenException

def require_roles_decorator(*role_names):
    """
    通用权限装饰器，用于检查当前认证用户是否拥有定义在 CustomUser 模型中的任意指定角色。
    例如：@require_roles_decorator('is_system_admin', 'is_space_manager')
    要求 request.user 已经通过 isAuthenticated 认证。
    """
    def decorator(view_method):
        @wraps(view_method)
        def _wrapped_view(self, request, *args, **kwargs):
            # 假设 IsAuthenticated 权限类已经执行，request.user 已经认证。
            # 这里添加一个防御性检查。
            if not request.user.is_authenticated:
                raise ForbiddenException("Authentication is required to perform this action.")

            has_required_role = False
            for role_name in role_names:
                # 动态检查 CustomUser 实例上的 @property 属性。
                # CustomUser 确保会定义这些属性，即使 AnonymousUser 也会有默认的 False。
                if getattr(request.user, role_name, False):
                    has_required_role = True
                    break

            if not has_required_role:
                raise ForbiddenException("您没有权限执行此操作。")

            return view_method(self, request, *args, **kwargs)
        return _wrapped_view
    return decorator

is_superuser_required = require_roles_decorator('is_superuser')
is_system_admin_required = require_roles_decorator('is_system_admin')
is_space_manager_required = require_roles_decorator('is_space_manager')
is_admin_or_space_manager_required = require_roles_decorator('is_system_admin', 'is_space_manager')
