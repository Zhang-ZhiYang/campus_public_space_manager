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

        # 请求用户是否是超级管理员
        is_requesting_superuser = request.user.is_super_admin
        # 请求用户是否是系统管理员 (非超级管理员)
        is_requesting_admin = request.user.is_admin and not is_requesting_superuser

        # 正在编辑的用户是否是超级管理员
        is_editing_superuser = obj and obj.is_super_admin

        # === 核心修改：明确处理权限逻辑，避免副作用 ===

        # 情景1: 非超级管理员用户尝试编辑超级管理员用户
        if is_editing_superuser and not is_requesting_superuser:
            # 禁用表单中所有字段
            for field_name in form.base_fields:
                form.base_fields[field_name].widget.attrs['disabled'] = True
                # 对于 ManyToManyField 和 ForeignKey 等，可能还需要优化显示为只读
            # 不允许通过这个表单进行保存
            self.has_change_permission = lambda r, o=None: False  # 仅针对当前表单请求生效

        # 情景2: 系统管理员 (非超级管理员) 编辑非超级管理员用户
        elif is_requesting_admin:
            # 禁用安全敏感字段
            for field_name in ['is_superuser', 'is_staff', 'user_permissions', 'groups']:
                if field_name in form.base_fields:
                    form.base_fields[field_name].widget.attrs['disabled'] = True

            # 角色字段特殊处理：系统管理员不能将用户角色设为超级管理员
            if 'role' in form.base_fields:
                form.base_fields['role'].queryset = form.base_fields['role'].queryset.exclude(name=ROLE_SUPERUSER)
                # 如果当前编辑的用户角色是超级管理员 (尽管 is_editing_superuser 应该已经处理了)
                if obj and obj.is_super_admin:
                    form.base_fields['role'].widget.attrs['disabled'] = True
                    # 确保即使禁用，仍显示正确的超级管理员角色
                    form.base_fields['role'].queryset = Role.objects.filter(name=ROLE_SUPERUSER)

        # ===============================================================
        return form

    def has_change_permission(self, request, obj=None):
        user = request.user
        if user.is_super_admin:  # 超级管理员拥有所有权限
            return True
        if user.is_admin:  # 系统管理员权限
            if obj and obj.is_super_admin:  # 系统管理员不能修改超级管理员账户
                return False
            return True  # 系统管理员可以修改其他非超级管理员用户
        return False  # 其他用户（如空间管理员、学生）无权在这里修改用户

    def has_add_permission(self, request):
        # 只有超级管理员和系统管理员可以添加新用户
        return request.user.is_super_admin or request.user.is_admin

    def has_delete_permission(self, request, obj=None):
        user = request.user
        if user.is_super_admin:
            return True
        if user.is_admin:
            if obj and obj.is_super_admin:  # 系统管理员不能删除超级管理员账户
                return False
            return True  # 系统管理员可以删除其他非超级管理员用户
        return False
