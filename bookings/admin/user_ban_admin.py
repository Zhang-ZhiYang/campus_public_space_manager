# bookings/admin/user_ban_admin.py
from django.contrib import admin
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q # 导入 Q

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

# 导入本应用的模型
from bookings.models import UserSpaceTypeBan
# 导入相关模型
from spaces.models import Space # 从 spaces 应用导入
from django.conf import settings
CustomUser = settings.AUTH_USER_MODEL # 如果需要 CustomUser

# ====================================================================
# UserSpaceTypeBan Admin (用户禁用记录管理)
# ====================================================================
@admin.register(UserSpaceTypeBan)
class UserSpaceTypeBanAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'user_display', 'space_type_display', 'start_date', 'end_date',
        'is_active', 'ban_policy_applied_display', 'reason', 'issued_by_display', 'issued_at'
    )
    list_filter = ('space_type', 'issued_at', 'user', 'issued_by')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'reason', 'space_type__name')
    date_hierarchy = 'issued_at'
    raw_id_fields = ('user', 'space_type', 'ban_policy_applied', 'issued_by')
    fieldsets = (
        (None, {'fields': ('user', 'space_type', ('start_date', 'end_date'), 'reason',)}),
        ('策略与记录', {'fields': ('ban_policy_applied', ('issued_by', 'issued_at'))}),
    )
    readonly_fields = ('issued_at', 'issued_by')

    def save_model(self, request, obj, form, change):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        # 权限检查与 ViolationAdmin 类似
        if request.user.is_superuser or request.user.is_system_admin:
            pass  # 超级用户和系统管理员拥有所有权限
        else:
            target_space_type_for_perm = obj.space_type
            if target_space_type_for_perm:
                managed_spaces = get_objects_for_user(request.user,
                                                      'spaces.can_manage_space_details',
                                                      klass=Space)
                if not managed_spaces.filter(space_type=target_space_type_for_perm).exists():
                    messages.error(request, f"您没有权限修改此禁用记录(ID: {obj.pk})，因为您不管理其所属的空间类型。",
                                   messages.ERROR);
                    return
            else:
                messages.error(request, f"您没有权限修改全局禁用记录(ID: {obj.pk})。", messages.ERROR);
                return

        if not obj.issued_by and isinstance(request.user, CustomUser): obj.issued_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='用户')
    def user_display(self, obj: 'UserSpaceTypeBan'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'UserSpaceTypeBan'):
        return obj.space_type.name if obj.space_type else '全局'

    @admin.display(description='策略')
    def ban_policy_applied_display(self, obj: 'UserSpaceTypeBan'):
        return str(obj.ban_policy_applied) if obj.ban_policy_applied else 'N/A'

    @admin.display(description='执行人员')
    def issued_by_display(self, obj: 'UserSpaceTypeBan'):
        return obj.issued_by.get_full_name if obj.issued_by else '系统自动'

    @admin.display(boolean=True, description='是否活跃')
    def is_active(self, obj: 'UserSpaceTypeBan'):
        return obj.end_date > timezone.now() if obj.end_date else True

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'space_type', 'ban_policy_applied', 'issued_by')

        # CRITICAL FIX: 先获取有权限管理的 Space 对象，再从 Space 中提取 SpaceType ID
        managed_spaces = get_objects_for_user(
            request.user, 'spaces.can_manage_space_details', klass=Space
        )
        managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
        managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

        # 过滤 UserSpaceTypeBan 记录，排除 space_type 为空（全局）的记录
        return qs.filter(space_type__id__in=managed_spacetype_ids).select_related('user', 'space_type',
                                                                                  'ban_policy_applied', 'issued_by')

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
        else:  # Global ban
            return request.user.is_superuser or request.user.is_system_admin

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission for change
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global ban
            return request.user.is_superuser or request.user.is_system_admin  # only superuser/system_admin can change global ban

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin