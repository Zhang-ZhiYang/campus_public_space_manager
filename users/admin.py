# users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, Role
from .models import ROLE_STUDENT, ROLE_SPACE_MANAGER, ROLE_ADMIN, ROLE_SUPERUSER  # 导入角色常量


# ====================================================================
# Role Admin (角色管理) - 完全基于 Django 权限
# ====================================================================
@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

    # 权限：所有方法都直接使用 request.user.has_perm
    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('users.view_role')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('users.view_role', obj)

    def has_add_permission(self, request):
        return request.user.has_perm('users.add_role')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('users.change_role', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('users.delete_role', obj)


# ====================================================================
# CustomUserAdmin (自定义用户管理) - 仍然基于is_super_admin/is_admin进行管理层的安全保护
# ====================================================================
@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('自定义信息', {
            'fields': (
                'full_name', 'phone_number', 'work_id', 'major',
                'student_class', 'gender', 'total_violation_count', 'role'
            ),
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('自定义信息', {
            'fields': (
                'full_name', 'phone_number', 'work_id', 'major',
                'student_class', 'gender', 'role'
            ),
        }),
    )

    list_display = UserAdmin.list_display + ('full_name', 'phone_number', 'work_id', 'role', 'total_violation_count')
    list_filter = UserAdmin.list_filter + ('role',)
    search_fields = ('username', 'full_name', 'phone_number', 'work_id', 'email')
    ordering = ('username',)

    # 权限控制：这里仍使用 CustomUser 的 @property 方法，因为它是在管理用户自身的“身份”和“权限等级”
    # 这是对管理其他模型（Space, Booking等）权限的更高一层的安全保护。
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        is_requesting_superuser = request.user.is_super_admin
        is_requesting_admin = request.user.is_admin and not is_requesting_superuser
        is_editing_superuser = obj and obj.is_super_admin

        # 如果非超级管理员尝试编辑超级管理员用户
        if is_editing_superuser and not is_requesting_superuser:
            for field_name in form.base_fields:
                form.base_fields[field_name].widget.attrs['disabled'] = True
        # 如果系统管理员 (非超级管理员) 编辑非超级管理员用户
        elif is_requesting_admin and obj is not None:  # obj is not None 确保不是在添加新用户
            # 禁用安全敏感字段
            for field_name in ['is_superuser', 'is_staff', 'user_permissions', 'groups']:
                if field_name in form.base_fields:
                    form.base_fields[field_name].widget.attrs['disabled'] = True

            # 角色字段特殊处理：系统管理员不能将用户角色改为超级管理员
            if 'role' in form.base_fields:
                form.base_fields['role'].queryset = form.base_fields['role'].queryset.exclude(name=ROLE_SUPERUSER)
                if obj.is_super_admin:  # 不应该发生，因为前面的is_editing_superuser保护
                    form.base_fields['role'].widget.attrs['disabled'] = True
                    form.base_fields['role'].queryset = Role.objects.filter(name=ROLE_SUPERUSER)

        return form

    # 权限方法：对 CustomUser 模型的管理，我们依然使用基于角色的检查，以保护核心管理功能
    def has_module_permission(self, request):
        return request.user.is_staff and (request.user.is_super_admin or request.user.is_admin)

    def has_view_permission(self, request, obj=None):
        return request.user.is_super_admin or request.user.is_admin

    def has_add_permission(self, request):
        return request.user.is_super_admin or request.user.is_admin

    def has_change_permission(self, request, obj=None):
        user = request.user
        if user.is_super_admin:
            return True
        if user.is_admin:
            if obj and obj.is_super_admin:  # 系统管理员不能修改超级管理员
                return False
            return True  # 系统管理员可以修改其他非超级管理员
        return False

    def has_delete_permission(self, request, obj=None):
        user = request.user
        if user.is_super_admin:
            return True
        if user.is_admin:
            if obj and obj.is_super_admin:  # 系统管理员不能删除超级管理员
                return False
            return True  # 系统管理员可以删除其他非超级管理员
        return False