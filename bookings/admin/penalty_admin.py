# bookings/admin/penalty_admin.py
from django.contrib import admin
from django.contrib import messages
from django.db.models import Q # 导入 Q

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

# 导入本应用的模型
from bookings.models import UserPenaltyPointsPerSpaceType
# 导入相关模型
from spaces.models import Space # 从 spaces 应用导入

# ====================================================================
# UserPenaltyPointsPerSpaceType Admin (用户违约点数管理)
# ====================================================================
@admin.register(UserPenaltyPointsPerSpaceType)
class UserPenaltyPointsPerSpaceTypeAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'user_display', 'space_type_display', 'current_penalty_points',
        'last_violation_at', 'last_ban_trigger_at', 'updated_at'
    )
    list_filter = ('space_type', 'current_penalty_points', 'user')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'space_type__name')
    date_hierarchy = 'updated_at'
    raw_id_fields = ('user', 'space_type')
    fieldsets = (
        (None, {'fields': ('user', 'space_type', 'current_penalty_points')}),
        ('时间信息', {'fields': ('last_violation_at', 'last_ban_trigger_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ('user', 'space_type', 'current_penalty_points', 'last_violation_at', 'last_ban_trigger_at',
                       'updated_at')

    @admin.display(description='用户')
    def user_display(self, obj: 'UserPenaltyPointsPerSpaceType'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'UserPenaltyPointsPerSpaceType'):
        return obj.space_type.name if obj.space_type else '全局'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'space_type')

        # assume SPACES_MODELS_LOADED is true if Space is imported successfully
        # if not SPACES_MODELS_LOADED:
        #     messages.warning(request,
        #                      "Space models not available. Penalty points cannot be filtered by space permissions.")
        #     return qs.none()

        # CRITICAL FIX: 先获取有权限管理的 Space 对象，再从 Space 中提取 SpaceType ID
        managed_spaces = get_objects_for_user(
            request.user, 'spaces.can_manage_space_details', klass=Space
        )
        managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
        managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

        # 过滤 UserPenaltyPointsPerSpaceType 记录，排除 space_type 为空（全局）的记录
        return qs.filter(space_type__id__in=managed_spacetype_ids).select_related('user', 'space_type')

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin or \
            (request.user.is_staff and request.user.is_space_manager)

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission based on SpaceType
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global penalty_points
            return request.user.is_superuser or request.user.is_system_admin

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False