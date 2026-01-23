# spaces/admin/space_type_admin.py
from django.contrib import admin
from django.contrib import messages  # 导入 messages
# 导入本应用的模型
from spaces.models import SpaceType, Space  # 导入 Space 用于权限检查
from guardian.shortcuts import get_objects_for_user  # 导入 guardian

# ====================================================================
# SpaceType Admin (空间类型管理)
# ====================================================================
@admin.register(SpaceType)
class SpaceTypeAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'is_basic_infrastructure', 'default_is_bookable', 'default_requires_approval',
        'default_check_in_method', # <--- 新增
        'default_available_start_time', 'default_available_end_time',
        'description'
    )
    search_fields = ('name',)
    list_filter = (
        'is_basic_infrastructure',
        'default_is_bookable', 'default_requires_approval',
        'default_check_in_method' # <--- 新增
    )
    ordering = ('name',)

    fieldsets = (
        (None, {'fields': ('name', 'description')}),
        ('类型属性', {'fields': ('is_basic_infrastructure',)}),
        ('默认预订规则 (创建空间时可作为默认值)', {
            'fields': (
                'default_is_bookable',
                'default_requires_approval',
                'default_check_in_method', # <--- 新增
                'default_available_start_time', 'default_available_end_time',
                'default_min_booking_duration', 'default_max_booking_duration',
                'default_buffer_time_minutes'
            ),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return qs

        # 空间管理员现在可以查看所有空间类型
        if getattr(request.user, 'is_space_manager', False):
            return qs # 返回所有空间类型，不进行过滤
        return qs.none() # 其他用户仍不能查看

    def has_module_permission(self, request):
        """
        统一的模块可见性权限检查。
        - 未登录用户：不可见。
        - 超级用户/系统管理员：总是可见。
        - 空间管理员：取决于是否被明确分配了该 Model 的 Django 默认 'view_xxx' 权限。
        - 其他用户：不可见。
        """
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            return True

        # 如果是空间管理员，动态获取当前模型的 app_label 和 model_name
        # 然后检查是否显式分配了该模型的默认 view_xxx 权限。
        if getattr(request.user, 'is_space_manager', False):
            app_label = self.opts.app_label
            model_name = self.opts.model_name
            permission_codename = f'{app_label}.view_{model_name}'
            return request.user.has_perm(permission_codename)

        return False

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        # 对于 SpaceManager，只要它能看到模块（通过 Django 默认 view_spacetype 权限），就可以查看任何 SpaceType 对象
        if getattr(request.user, 'is_space_manager', False):
            return True # 允许查看任何空间类型的详情页
        return False

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        # 空间管理员不能添加
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        # 空间管理员不能修改
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        # 空间管理员不能删除
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)