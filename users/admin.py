# users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group # 导入 Group
from .models import CustomUser

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    # fieldsets 中的 'role' 字段已移除
    fieldsets = UserAdmin.fieldsets + (
        ('自定义信息', {
            'fields': (
                'phone_number', 'work_id', 'major',
                'student_class', 'gender', # 'total_violation_count' 字段已移除
            ),
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('自定义信息', {
            'fields': (
                'phone_number', 'work_id', 'major',
                'student_class', 'gender',
            ),
        }),
    )

    # list_display 和 list_filter 中的 'role' 字段已移除
    list_display = UserAdmin.list_display + ('get_full_name', 'phone_number', 'work_id', 'get_groups_display')
    list_filter = UserAdmin.list_filter + ('groups',) # 调整为按 groups 过滤
    search_fields = ('username', 'first_name', 'last_name', 'phone_number', 'work_id', 'email') # 调整 search_fields
    ordering = ('username',)

    # 新增方法以显示用户所属的组
    def get_groups_display(self, obj):
        return ", ".join([g.name for g in obj.groups.all()])
    get_groups_display.short_description = '所属组'

    # 重写 get_form 以提供更细粒度的管理控制
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        is_requesting_superuser = request.user.is_superuser
        is_requesting_system_admin = request.user.is_system_admin and not is_requesting_superuser # 非超级管理员的系统管理员
        is_editing_superuser = obj and obj.is_superuser

        # 如果非超级管理员尝试编辑或添加超级管理员用户
        if is_editing_superuser and not is_requesting_superuser:
            for field_name in form.base_fields:
                form.base_fields[field_name].widget.attrs['disabled'] = True
                form.base_fields[field_name].required = False # 也设置为非必填，以免保存时报错
        # 如果系统管理员 (非超级管理员) 编辑非超级管理员用户
        elif is_requesting_system_admin and obj is not None: # obj is not None 确保不是在添加新用户
            # 禁用安全敏感字段
            for field_name in ['is_superuser', 'user_permissions']: # 'is_staff' 字段现在由 save 方法自动管理
                if field_name in form.base_fields:
                    form.base_fields[field_name].widget.attrs['disabled'] = True
                    form.base_fields[field_name].required = False

            # Group 字段特殊处理：系统管理员不能将用户添加到'系统管理员'或'超级管理员'组 (或者不能从这些组移除)
            # 或者限制其只能添加/移除特定组
            if 'groups' in form.base_fields:
                # 获取系统管理员和超级管理员组
                sys_admin_group = Group.objects.filter(name='系统管理员').first()
                # superuser_group = Group.objects.filter(name='超级管理员').first() # Django 默认没有 '超级管理员' group

                # 限制系统管理员不能将用户添加到 '系统管理员' 组
                # 更精细的控制可以是通过自定义 ManyToMany 字段 widgets
                # 这里简单处理，例如不允许编辑自己的 Group 或编辑其他关键 Group
                # 实际操作中，最好是限制 QuerySet，或者在 save_model 中进行最终校验
                pass # 这里不直接禁用，因为 ManyToManyField 禁用通常需要自定义 Widget 或 save_model 校验

        return form

    # 权限方法：对 CustomUser 模型的管理，我们使用基于 Group 的属性来控制权限
    def has_module_permission(self, request):
        """只有能登录后台的管理员和空间管理员才能看到用户模块"""
        return request.user.is_staff and (request.user.is_system_admin or request.user.is_space_manager)

    def has_view_permission(self, request, obj=None):
        """系统管理员和空间管理员可以查看用户"""
        return request.user.is_system_admin or request.user.is_space_manager

    def has_add_permission(self, request):
        """只有系统管理员可以添加用户"""
        return request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        user = request.user
        if user.is_superuser: # 超级管理员可以修改所有用户
            return True
        if user.is_system_admin: # 系统管理员可以修改非超级管理员用户
            if obj and obj.is_superuser:
                return False
            return True
        return False

    def has_delete_permission(self, request, obj=None):
        user = request.user
        if user.is_superuser: # 超级管理员可以删除所有用户
            return True
        if user.is_system_admin: # 系统管理员可以删除非超级管理员用户
            if obj and obj.is_superuser:
                return False
            return True
        return False