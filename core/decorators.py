# core/decorators.py
from functools import wraps
from core.utils.exceptions import ForbiddenException

def require_roles_decorator(*role_names):
    """
    通用权限装饰器，用于检查当前认证用户是否拥有定义在 CustomUser 模型中的任意指定角色。
    针对方法签名为 `(self, request, *args, **kwargs)` 的视图方法。
    """
    def decorator(view_method):
        @wraps(view_method)
        def _wrapped_view(self, request, *args, **kwargs): # 注意这里有 'request' 参数
            if not request.user.is_authenticated:
                raise ForbiddenException("Authentication is required to perform this action.")

            has_required_role = False
            for role_name in role_names:
                if getattr(request.user, role_name, False):
                    has_required_role = True
                    break

            if not has_required_role:
                raise ForbiddenException("您没有权限执行此操作。")

            return view_method(self, request, *args, **kwargs)
        return _wrapped_view
    return decorator

# --- 新增的装饰器类型 ---
def require_roles_for_self_request_methods(*role_names):
    """
    通用权限装饰器，用于检查当前认证用户是否拥有定义在 CustomUser 模型中的任意指定角色。
    针对方法签名为 `(self, *args, **kwargs)` 的视图方法，通过 `self.request` 访问请求对象。
    """
    def decorator(view_method):
        @wraps(view_method)
        def _wrapped_view(self, *args, **kwargs): # 注意这里没有 'request' 参数
            if not self.request.user.is_authenticated: # 从 self.request 访问 user
                raise ForbiddenException("Authentication is required to perform this action.")

            has_required_role = False
            for role_name in role_names:
                if getattr(self.request.user, role_name, False): # 从 self.request 访问 user
                    has_required_role = True
                    break

            if not has_required_role:
                raise ForbiddenException("您没有权限执行此操作。")

            return view_method(self, *args, **kwargs)
        return _wrapped_view
    return decorator

# 现有的公共装饰器 (保持不变)
is_superuser_required = require_roles_decorator('is_superuser')
is_system_admin_required = require_roles_decorator('is_system_admin')
is_space_manager_required = require_roles_decorator('is_space_manager')
is_admin_or_space_manager_required = require_roles_decorator('is_system_admin', 'is_space_manager')

# --- 新增的公共装饰器实例 ---
is_system_admin_for_qs_obj = require_roles_for_self_request_methods('is_system_admin')
is_admin_or_space_manager_for_qs_obj = require_roles_for_self_request_methods('is_system_admin', 'is_space_manager')