# users/permissions.py
from rest_framework import permissions
from users.models import ROLE_ADMIN, ROLE_SPACE_MANAGER, ROLE_SUPERUSER  # 导入角色常量


class IsAdminOrSpaceManagerOrReadOnly(permissions.BasePermission):
    """
    自定义权限，允许管理员或空间管理员完全访问（读写），其他认证用户只读。
    """

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user.is_authenticated

        return request.user.is_authenticated and (
                request.user.is_admin or
                request.user.is_space_manager  # 使用 CustomUser 的辅助方法
        )


class IsAdminOrSuperAdmin(permissions.BasePermission):
    """
    自定义权限，只有系统管理员或超级管理员拥有完全访问权限。
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            request.user.is_admin  # 使用 CustomUser 的辅助方法
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class IsAdminOrSpaceManager(permissions.BasePermission):
    """
    自定义权限，只有系统管理员、空间管理员或超级管理员拥有完全访问权限。
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated and (
                request.user.is_admin or
                request.user.is_space_manager  # 使用 CustomUser 的辅助方法
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class IsSuperAdmin(permissions.BasePermission):
    """
    只有超级管理员才能操作，例如修改用户角色。
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_super_admin  # 使用 CustomUser 的辅助方法

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)