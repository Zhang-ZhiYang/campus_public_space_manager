# users/permissions.py
from rest_framework import permissions
# 不再需要导入具体的角色常量，因为 CustomUser 上的 @property 方法会处理

class IsAdminOrSpaceManagerOrReadOnly(permissions.BasePermission):
    """
    自定义权限，允许系统管理员或空间管理员完全访问（读写），其他认证用户只读。
    """
    message = "您没有权限执行此操作。"

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False # 未认证用户不允许任何操作

        # 允许 GET, HEAD, OPTIONS 请求给所有认证用户
        if request.method in permissions.SAFE_METHODS:
            return True

        # 否则，只允许系统管理员或空间管理员进行写操作 (POST, PUT, PATCH, DELETE)
        return request.user.is_system_admin or request.user.is_space_manager

class IsSystemAdminOnly(permissions.BasePermission): # 重命名为更清晰的 SystemAdminOnly
    """
    自定义权限，只有系统管理员（包括is_superuser）拥有完全访问权限。
    """
    message = "只有系统管理员才能执行此操作。"

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_system_admin

    # 对于对象级权限，如果只检查模型级别，has_object_permission 可以直接返回 has_permission
    # 但如果未来需要对象级权限，则需要具体实现
    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)

class IsSpaceManagerOrCheckinStaffOrSystemAdmin(permissions.BasePermission):
    """
    自定义权限，系统管理员、空间管理员或签到员拥有完全访问权限。
    适用于签到接口等场景。
    """
    message = "只有系统管理员、空间管理员或签到员才能执行此操作。"

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or \
               request.user.is_space_manager or \
               request.user.groups.filter(name='签到员').exists() # 直接检查 Group

class IsSuperuserOnly(permissions.BasePermission): # 新增或重命名，直接检查 is_superuser
    """
    只有超级管理员才能操作。
    """
    message = "只有超级管理员才能执行此操作。"

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_superuser

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)