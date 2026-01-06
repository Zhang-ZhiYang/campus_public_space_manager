# bookings/admin/violation_admin.py
from django.contrib import admin
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

from bookings.admin.booking_admin import SPACES_MODELS_LOADED
from bookings.models import Violation
from spaces.models import Space


@admin.register(Violation)
class ViolationAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'user_display', 'booking_id_display', 'violation_type', 'space_type_display', 'penalty_points',
        'issued_at', 'is_resolved', 'resolved_at_display', 'issued_by_display', 'resolved_by_display'
    )
    list_filter = ('violation_type', 'is_resolved', 'issued_at', 'user', 'issued_by', 'space_type')
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name',
        'booking__space__name', 'booking__bookable_amenity__amenity__name',
        'description', 'space_type__name'
    )
    date_hierarchy = 'issued_at'
    raw_id_fields = ('user', 'booking', 'issued_by', 'resolved_by', 'space_type')
    fieldsets = (
        (None, {'fields': (('user', 'booking'), 'violation_type', 'space_type', 'description',
                           ('penalty_points', 'is_resolved'))}),
        ('记录与解决信息', {'fields': (('issued_by', 'issued_at'), ('resolved_by', 'resolved_at'))}),
    )
    readonly_fields = ('issued_at',)

    def save_model(self, request, obj: 'Violation', form, change):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        if not obj.space_type and obj.booking:
            if obj.booking.space and obj.booking.space.space_type:
                obj.space_type = obj.booking.space.space_type
            elif obj.booking.bookable_amenity and obj.booking.bookable_amenity.space \
                    and obj.booking.bookable_amenity.space.space_type:
                obj.space_type = obj.bookable_amenity.space.space_type

        # 注意：这里管理权限 'spaces.can_manage_space_details' 是针对 Space 模型的，
        # obj.space_type 是 SpaceType 模型。
        # 这里需要更精细的权限检查，如果一个空间管理员管理一个 SpaceType 下的任何一个 Space，
        # 则可以修改该 SpaceType 相关的 Violation。

        # 检查用户是否是超级用户/系统管理员
        if request.user.is_superuser or request.user.is_system_admin:
            # 超级用户和系统管理员拥有所有权限，直接跳过对象权限检查
            pass
        else:
            # 对于空间管理员，检查他们是否有权限通过其管理的 Space 来管理这个 Violation 所属的 SpaceType
            target_space_type_for_perm = obj.space_type
            if target_space_type_for_perm:
                # 尝试获取用户有 'spaces.can_manage_space_details' 权限的所有 Space 对象
                managed_spaces = get_objects_for_user(request.user,
                                                      'spaces.can_manage_space_details',
                                                      klass=Space)
                # 检查是否存在任何一个被管理的 Space 属于这个 Violation 的 SpaceType
                if not managed_spaces.filter(space_type=target_space_type_for_perm).exists():
                    messages.error(request, f"您没有权限修改此违规记录(ID: {obj.pk})，因为您不管理其所属的空间类型。",
                                   messages.ERROR);
                    return
            else:
                # 如果 SpaceType 为空，通常表示全局违规，普通空间管理员无权修改
                messages.error(request, f"您没有权限修改全局违规记录(ID: {obj.pk})。", messages.ERROR);
                return

        if obj.is_resolved and not obj.resolved_at:
            obj.resolved_at = timezone.now()
            obj.resolved_by = request.user
        elif not obj.is_resolved and obj.resolved_at:
            obj.resolved_at = None
            obj.resolved_by = None

        super().save_model(request, obj, form, change)

    @admin.display(description='预订ID')
    def booking_id_display(self, obj: 'Violation'):
        return obj.booking.id if obj.booking else 'N/A'

    @admin.display(description='用户')
    def user_display(self, obj: 'Violation'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='记录人员')
    def issued_by_display(self, obj: 'Violation'):
        return obj.issued_by.get_full_name if obj.issued_by else 'N/A'

    @admin.display(description='解决人员')
    def resolved_by_display(self, obj: 'Violation'):
        return obj.resolved_by.get_full_name if obj.resolved_by else 'N/A'

    @admin.display(description='解决时间')
    def resolved_at_display(self, obj: 'Violation'):
        return obj.resolved_at if obj.resolved_at else '未解决'

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'Violation'):
        return obj.space_type.name if obj.space_type else 'N/A'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'booking__space', 'booking__bookable_amenity__space',
                                     'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type')
        if not SPACES_MODELS_LOADED:
            messages.warning(request, "Space models not available. Violations cannot be filtered by space permissions.")
            return qs.none()

        # CRITICAL FIX: 先获取有权限管理的 Space 对象，再从 Space 中提取 SpaceType ID
        # Permission 'spaces.can_manage_space_details' is defined on Space model, so klass=Space
        managed_spaces = get_objects_for_user(
            request.user, 'spaces.can_manage_space_details', klass=Space
        )
        # 从这些 Space 对象中获取所有关联的 SpaceType 的 ID (去重，并去除 None 值)
        managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
        managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

        # 过滤 Violation 记录，使其与用户管理的 SpaceType 相关联
        # 排除 space_type 为空（全局）的记录，因为空间管理员不应看到全局记录
        return qs.filter(
            Q(space_type__id__in=managed_spacetype_ids) |
            Q(booking__space__space_type__id__in=managed_spacetype_ids) |
            Q(booking__bookable_amenity__space__space_type__id__in=managed_spacetype_ids)
        ).distinct().select_related('user', 'booking__space', 'booking__bookable_amenity__space',
                                    'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type')

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        return request.user.is_staff and request.user.is_space_manager

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission based on SpaceType associated with the Violation
        # If the obj has a space_type, check if the user manages any Space under that SpaceType.
        # Otherwise, if it's a global violation, only superusers/system_admins can view.
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global violation (space_type is None)
            return request.user.is_superuser or request.user.is_system_admin


    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        # 仅允许系统管理员和超级管理员添加违约记录，移除空间管理员的权限
        return request.user.is_superuser or request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission for change, similar to has_view_permission.
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global violation (space_type is None)
            return request.user.is_superuser or request.user.is_system_admin

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser

    def get_actions(self, request):
        if not request.user.is_authenticated: return {}
        actions = super().get_actions(request)

        if not (request.user.is_superuser or request.user.is_system_admin):
            actions.pop('delete_selected', None)
            allowed_actions_for_space_manager = ['mark_resolved']
            all_current_actions = list(actions.keys())
            for action_name in all_current_actions:
                if action_name not in allowed_actions_for_space_manager:
                    actions.pop(action_name, None)

            if 'mark_resolved' not in actions:
                actions['mark_resolved'] = self.get_action('mark_resolved')

        return actions

    @admin.action(description="解决选择的违约记录")
    def mark_resolved(self, request, queryset):
        if not request.user.is_authenticated: self.message_user(request, "您没有权限执行此操作，请先登录。",
                                                                messages.ERROR); return
        resolved_count = 0
        for violation in queryset:
            # 权限检查与 save_model/has_change_permission 类似
            if request.user.is_superuser or request.user.is_system_admin:
                can_resolve = True
            elif violation.space_type:
                managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
                can_resolve = managed_spaces.filter(space_type=violation.space_type).exists()
            else:  # Global violation
                can_resolve = False

            if can_resolve:
                if not violation.is_resolved:
                    violation.is_resolved = True
                    violation.resolved_by = request.user
                    violation.resolved_at = timezone.now()
                    violation.save(update_fields=['is_resolved', 'resolved_by', 'resolved_at'])
                    resolved_count += 1
                else:
                    self.message_user(request, f"违规 {violation.id} 已是解决状态。", messages.WARNING)
            else:
                self.message_user(request, f"您没有权限解决违规 {violation.id}。", messages.ERROR)
        self.message_user(request, f"成功解决了 {resolved_count} 条违约记录。", messages.SUCCESS)

