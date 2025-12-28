# users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, Role
from .models import ROLE_STUDENT, ROLE_SPACE_MANAGER, ROLE_ADMIN, ROLE_SUPERUSER  # 导入角色常量


# ====================================================================
# Role Admin (角色管理)
# ====================================================================
@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

    def has_add_permission(self, request):
        return request.user.is_super_admin  # 只有超级管理员可以添加角色

    def has_change_permission(self, request, obj=None):
        return request.user.is_super_admin  # 只有超级管理员可以修改角色

    def has_delete_permission(self, request, obj=None):
        return request.user.is_super_admin  # 只有超级管理员可以删除角色


# ====================================================================
# CustomUserAdmin (自定义用户管理)
# ====================================================================
@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    # 继承 UserAdmin，大部分功能已具备

    # 添加自定义字段到 fieldsets
    fieldsets = UserAdmin.fieldsets + (
        ('自定义信息', {
            'fields': (
                'full_name', 'phone_number', 'work_id', 'major',
                'student_class', 'gender', 'total_violation_count', 'role'
            ),
        }),
    )
    # 添加自定义字段到 add_fieldsets (用于新建用户时的表单)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('自定义信息', {
            'fields': (
                'full_name', 'phone_number', 'work_id', 'major',
                'student_class', 'gender', 'role'  # 新建时通常不设置 total_violation_count
            ),
        }),
    )

    list_display = UserAdmin.list_display + ('full_name', 'phone_number', 'work_id', 'role', 'total_violation_count')
    list_filter = UserAdmin.list_filter + ('role',)  # 按角色过滤
    search_fields = ('username', 'full_name', 'phone_number', 'work_id', 'email')
    ordering = ('username',)

    # ================================================================
    # 权限控制：只有超级管理员可以修改用户的 is_staff/is_superuser 状态及角色
    # 系统管理员可以修改普通用户（学生/空间管理员）的非敏感信息
    # ================================================================
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        is_superuser = request.user.is_superuser
        is_admin = request.user.is_admin  # 使用自定义辅助方法

        # 超级管理员可以编辑所有字段
        if is_superuser:
            pass
        # 系统管理员可以编辑非超级管理员的用户，但不能修改 is_superuser, is_staff, user_permissions, groups 字段
        elif is_admin:
            if obj and obj.is_superuser:  # 系统管理员不能编辑超级管理员
                self.readonly_fields = [f.name for f in self.model._meta.fields] + ['user_permissions', 'groups']
                # 隐藏 save 按钮
                self.has_change_permission = lambda r, o=None: False
                return form

            # 系统管理员不能修改 is_superuser, is_staff 和 role 字段为更高权限
            # 这里的逻辑可能需要更精细，例如，系统管理员可以任命空间管理员
            # 目前简化为：系统管理员可以修改 role 但不能设为超级管理员，不能修改 is_superuser/is_staff
            form.base_fields['is_superuser'].widget.attrs['disabled'] = True
            form.base_fields['is_staff'].widget.attrs['disabled'] = True
            if 'user_permissions' in form.base_fields:
                form.base_fields['user_permissions'].widget.attrs['disabled'] = True
            if 'groups' in form.base_fields:
                form.base_fields['groups'].widget.attrs['disabled'] = True

            # 系统管理员不能将角色设置为超级管理员
            if 'role' in form.base_fields:
                role_choices = list(form.base_fields['role'].queryset.all())
                form.base_fields['role'].queryset = form.base_fields['role'].queryset.exclude(name=ROLE_SUPERUSER)
                # 如果当前用户的角色是超级管理员，显示为不可编辑，并清空所有其他选项
                if obj and obj.is_super_admin:
                    form.base_fields['role'].widget.attrs['disabled'] = True
                    form.base_fields['role'].queryset = Role.objects.filter(name=ROLE_SUPERUSER)  # 仅显示当前角色

        return form

    def has_change_permission(self, request, obj=None):
        user = request.user
        if user.is_superuser:
            return True
        if user.is_admin:
            # 系统管理员不能修改超级管理员账户
            if obj and obj.is_superuser:
                return False
            return True  # 系统管理员可以修改其他普通用户
        return False  # 其他用户无权修改

    def has_add_permission(self, request):
        # 只有超级管理员和系统管理员可以添加新用户
        return request.user.is_superuser or request.user.is_admin

    def has_delete_permission(self, request, obj=None):
        user = request.user
        if user.is_superuser:
            return True
        if user.is_admin:
            # 系统管理员不能删除超级管理员账户
            if obj and obj.is_superuser:
                return False
            return True  # 系统管理员可以删除其他普通用户
        return False  # 其他用户无权删除
