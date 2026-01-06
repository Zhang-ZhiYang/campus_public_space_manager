# bookings/admin/ban_policy_admin.py
from django.contrib import admin
from django.contrib import messages
from django.db.models import Q # 导入 Q

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

# 导入本应用的模型
from bookings.models import SpaceTypeBanPolicy
# 导入相关模型
from spaces.models import Space # 从 spaces 应用导入

# ====================================================================
# SpaceTypeBanPolicy Admin (空间类型禁用策略管理)
# ====================================================================
@admin.register(SpaceTypeBanPolicy)
class SpaceTypeBanPolicyAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'space_type_display', 'threshold_points', 'ban_duration',
        'priority', 'is_active', 'description'
    )
    list_filter = ('is_active', 'space_type', 'priority')
    search_fields = ('description', 'space_type__name')
    raw_id_fields = ('space_type',)
    fieldsets = (
        (None,
         {'fields': ('space_type', ('threshold_points', 'ban_duration'), 'priority', 'is_active', 'description')}),
        ('系统信息', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)})
    )
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'SpaceTypeBanPolicy'):
        return obj.space_type.name if obj.space_type else '全局'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()

        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('space_type')

        # CRITICAL FIX: 空间管理员应该只看到他们管理的 SpaceType 相关的禁用策略
        if request.user.is_staff and request.user.is_space_manager:
            # Check for Space model availability (simplified check for this file)
            try:
                from spaces.models import Space
            except ImportError:
                messages.warning(request,
                                 "Space models not available. Ban policies cannot be filtered by space permissions.")
                return qs.none()

            managed_spaces = get_objects_for_user(
                request.user, 'spaces.can_manage_space_details', klass=Space  # klass 匹配 Space
            )
            managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
            managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

            # 过滤 SpaceTypeBanPolicy 记录，排除 space_type 为空（全局）的记录
            return qs.filter(space_type__id__in=managed_spacetype_ids).select_related('space_type')

        return qs.none()  # Default for other non-staff users

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # 视图权限：如果是一个具体的策略，检查其 SpaceType 是否被当前用户管理
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # 全局策略
            return request.user.is_superuser or request.user.is_system_admin

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin