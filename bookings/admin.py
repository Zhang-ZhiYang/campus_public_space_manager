# bookings/admin.py
from django.contrib import admin
from django.db import transaction
from django.contrib import messages
from django.utils import timezone
from django.forms import ModelForm

# 直接导入本应用的模型
from bookings.models import (
    Booking,
    Violation,
    UserPenaltyPointsPerSpaceType,
    SpaceTypeBanPolicy,
    UserSpaceTypeBan,
    UserSpaceTypeExemption
)

# 导入 CustomUser 和 Space 相关模型
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from users.models import CustomUser
    from spaces.models import Space, SpaceType, BookableAmenity

# 用于运行时安全导入及模拟其他应用的模型
try:
    from users.models import CustomUser
    from spaces.models import Space, SpaceType, BookableAmenity
except ImportError:
    class CustomUser:  # Mock CustomUser for other apps if `users` is not ready
        is_authenticated = False
        is_staff = False
        has_perm = lambda self, perm_name, obj=None: False
        username = "mock_user"

        def get_full_name(self): return self.username


    class Space:  # Mock Space
        name = "Mock Space"
        requires_approval = False

        def __str__(self): return self.name


    class SpaceType:  # Mock SpaceType
        name = "Mock SpaceType"

        def __str__(self): return self.name


    class BookableAmenity:  # Mock BookableAmenity
        amenity = SpaceType()  # Simple mock attribute
        space = Space()  # Simple mock attribute

        def __str__(self): return "Mock BookableAmenity"


    print("Warning: Missing modules (users.models.CustomUser, spaces.models.Space, etc.). "
          "Using mock objects for *external* models in bookings/admin.py. Admin functionalities may be limited.")


# ====================================================================
# Booking Admin (预订管理)
# ====================================================================
@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user_display', 'booking_target_display', 'status', 'booked_quantity',
        'start_time', 'end_time', 'reviewed_by_display', 'requires_approval_status'
    )
    list_filter = (
        'status', 'start_time', 'end_time',
        'space__space_type',  # 按空间类型过滤
        'space',  # 按具体空间过滤
        'bookable_amenity__amenity',  # 按设施类型过滤
        'user',  # 按用户过滤
        'reviewed_by'  # 按审核人员过滤
    )
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name', 'purpose',
        'space__name', 'bookable_amenity__space__name',
        'bookable_amenity__amenity__name'  # 增加搜索字段
    )
    raw_id_fields = ('user', 'space', 'bookable_amenity', 'reviewed_by')
    date_hierarchy = 'start_time'

    fieldsets = (
        (None, {
            'fields': (('user', 'status'), ('space', 'bookable_amenity', 'booked_quantity'), 'purpose',)
        }),
        ('时间信息', {
            'fields': (('start_time', 'end_time'),)
        }),
        ('审核信息', {
            'fields': ('admin_notes', ('reviewed_by', 'reviewed_at'))
        }),
        ('系统信息', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    # readonly_fields 应该包含所有自动填充或不应手动修改的字段
    readonly_fields = ('created_at', 'updated_at', 'reviewed_at')

    actions = ['approve_bookings', 'reject_bookings', 'cancel_bookings', 'mark_completed_bookings', 'mark_checked_in']

    @admin.display(description='预订用户')
    def user_display(self, obj: 'Booking'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='审核者')
    def reviewed_by_display(self, obj: 'Booking'):
        return obj.reviewed_by.get_full_name if obj.reviewed_by else 'N/A'

    @admin.display(description='预订目标')
    def booking_target_display(self, obj: 'Booking'):
        if obj.bookable_amenity:
            return f"设施: {obj.bookable_amenity.amenity.name} in {obj.bookable_amenity.space.name}"
        elif obj.space:
            return f"空间: {obj.space.name}"
        return "N/A"

    @admin.display(description='是否需审批')
    def requires_approval_status(self, obj: 'Booking'):
        target_obj = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        return "是" if target_obj and target_obj.requires_approval else "否"

    def get_queryset(self, request):
        # 预加载相关数据，减少 N+1 查询
        qs = super().get_queryset(request)
        return qs.select_related(
            'user', 'reviewed_by', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
        )

    # --- 权限检查方法 (使用 Django Permissions) ---
    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.view_booking')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('bookings.view_booking', obj)

    def has_add_permission(self, request):
        return request.user.has_perm('bookings.add_booking')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('bookings.change_booking', obj)  # 对象级权限可在此处集成

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('bookings.delete_booking', obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        # 只有有 'change_booking' 权限的用户才能看到和使用自定义动作
        if not request.user.has_perm('bookings.change_booking'):
            actions_to_remove = [action for action in self.actions if action in actions]
            for action_name in actions_to_remove:
                del actions[action_name]

        # 针对批量删除的权限检查
        if 'delete_selected' in actions and not request.user.has_perm('bookings.delete_booking'):
            del actions['delete_selected']
        return actions

    # --- Action 方法定义 ---
    @admin.action(description="批准选择的预订")
    def approve_bookings(self, request, queryset):
        if not request.user.has_perm('bookings.change_booking'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.filter(status='PENDING').update(
            status='APPROVED',
            reviewed_by=request.user,  # 记录操作者
            reviewed_at=timezone.now()
        )
        self.message_user(request, f"成功批准了 {updated_count} 条预订。", messages.SUCCESS)

    @admin.action(description="拒绝选择的预订")
    def reject_bookings(self, request, queryset):
        if not request.user.has_perm('bookings.change_booking'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.filter(status='PENDING').update(
            status='REJECTED',
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        self.message_user(request, f"成功拒绝了 {updated_count} 条预订。", messages.SUCCESS)

    @admin.action(description="取消选择的预订")
    def cancel_bookings(self, request, queryset):
        if not request.user.has_perm('bookings.change_booking'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        # 允许取消 PENDING, APPROVED, CHECKED_IN 状态的预订
        updated_count = queryset.filter(status__in=['PENDING', 'APPROVED', 'CHECKED_IN']).update(
            status='CANCELLED',
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        self.message_user(request, f"成功取消了 {updated_count} 条预订。", messages.SUCCESS)

    @admin.action(description="标记为已完成")
    def mark_completed_bookings(self, request, queryset):
        if not request.user.has_perm('bookings.change_booking'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        # 仅将已签到或已批准的标记为完成
        updated_count = queryset.filter(status__in=['APPROVED', 'CHECKED_IN']).update(
            status='COMPLETED',
        )
        self.message_user(request, f"成功标记 {updated_count} 条预订为已完成。", messages.SUCCESS)

    @admin.action(description="标记为已签到")
    def mark_checked_in(self, request, queryset):
        if not request.user.has_perm('bookings.change_booking'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        # 仅将已批准的预订标记为已签到
        updated_count = queryset.filter(status='APPROVED').update(
            status='CHECKED_IN',
        )
        self.message_user(request, f"成功标记 {updated_count} 条预订为已签到。", messages.SUCCESS)


# ====================================================================
# Violation Admin (违约记录管理)
# ====================================================================
@admin.register(Violation)
class ViolationAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user_display', 'violation_type', 'space_type_display', 'penalty_points',
        'issued_at', 'is_resolved', 'resolved_at_display', 'issued_by_display', 'resolved_by_display'
    )
    list_filter = ('violation_type', 'is_resolved', 'issued_at', 'user', 'issued_by', 'space_type')  # 增加 space_type
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name',
        'booking__purpose', 'description', 'space_type__name'  # 增加搜索字段
    )
    date_hierarchy = 'issued_at'
    raw_id_fields = ('user', 'booking', 'issued_by', 'resolved_by', 'space_type')  # 增加 resolved_by, space_type

    fieldsets = (
        (None, {
            'fields': (
                ('user', 'booking'),
                'violation_type',
                'space_type',  # 显示 space_type
                'description',
                ('penalty_points', 'is_resolved')
            )
        }),
        ('记录与解决信息', {
            'fields': (('issued_by', 'issued_at'), ('resolved_by', 'resolved_at'))  # 增加解决信息
        }),
    )
    # readonly_fields 应该包含自动填充或不应手动修改的字段
    readonly_fields = ('issued_at', 'resolved_at')

    # 为 'is_resolved' 字段添加一个自定义的保存逻辑，确保 resolved_at 和 resolved_by 自动填充
    def save_model(self, request, obj: 'Violation', form, change):
        if obj.is_resolved and not obj.resolved_at:
            obj.resolved_at = timezone.now()
            obj.resolved_by = request.user if isinstance(request.user, CustomUser) else None
        elif not obj.is_resolved and obj.resolved_at:  # 如果从已解决变回未解决，清空解决信息
            obj.resolved_at = None
            obj.resolved_by = None

        # 确保 space_type 字段在 save_model 之前被正确赋值 (如果预订存在)
        if not obj.space_type and obj.booking:
            if obj.booking.space and obj.booking.space.space_type:
                obj.space_type = obj.booking.space.space_type
            elif obj.booking.bookable_amenity and obj.booking.bookable_amenity.space and obj.booking.bookable_amenity.space.space_type:
                obj.space_type = obj.booking.bookable_amenity.space.space_type

        super().save_model(request, obj, form, change)

    @admin.display(description='违约用户')
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
        return qs.select_related(
            'user', 'booking', 'issued_by', 'resolved_by', 'space_type'
        )

    # --- 权限检查方法 (使用 Django Permissions) ---
    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.view_violation')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('bookings.view_violation', obj)

    def has_add_permission(self, request):
        return request.user.has_perm('bookings.add_violation')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('bookings.change_violation', obj)  # 对象级权限可在此处集成

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('bookings.delete_violation', obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        # 确保只有有 'change_violation' 权限的用户才能看到和使用自定义动作
        if not request.user.has_perm('bookings.change_violation'):
            actions_to_remove = [action for action in self.actions if action in actions and action != 'delete_selected']
            for action_name in actions_to_remove:
                del actions[action_name]

        # 针对批量删除的权限检查
        if 'delete_selected' in actions and not request.user.has_perm('bookings.delete_violation'):
            del actions['delete_selected']

        # 可以添加一个批量解决的动作
        if request.user.has_perm('bookings.change_violation') and 'mark_resolved' not in actions:
            # --- 修复 TypeError 的关键行 ---
            actions['mark_resolved'] = self.get_action('mark_resolved')
        return actions

    @admin.action(description="解决选择的违约记录")
    def mark_resolved(self, request, queryset):
        if not request.user.has_perm('bookings.change_violation'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return

        # 确保只有未解决的记录才被标记为解决
        updated_count = queryset.filter(is_resolved=False).update(
            is_resolved=True,
            resolved_at=timezone.now(),
            resolved_by=request.user if isinstance(request.user, CustomUser) else None
        )
        self.message_user(request, f"成功解决了 {updated_count} 条违约记录。", messages.SUCCESS)


# ====================================================================
# UserPenaltyPointsPerSpaceType Admin (用户违约点数管理)
# ====================================================================
@admin.register(UserPenaltyPointsPerSpaceType)
class UserPenaltyPointsPerSpaceTypeAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user_display', 'space_type_display', 'current_penalty_points',
        'last_violation_at', 'last_ban_trigger_at', 'updated_at'
    )
    list_filter = ('space_type', 'current_penalty_points', 'user')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'space_type__name')
    date_hierarchy = 'updated_at'
    raw_id_fields = ('user', 'space_type')

    fieldsets = (
        (None, {
            'fields': ('user', 'space_type', 'current_penalty_points')
        }),
        ('时间信息', {
            'fields': ('last_violation_at', 'last_ban_trigger_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    readonly_fields = ('last_violation_at', 'last_ban_trigger_at', 'updated_at')  # 这些字段应由系统自动更新

    @admin.display(description='用户')
    def user_display(self, obj: 'UserPenaltyPointsPerSpaceType'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'UserPenaltyPointsPerSpaceType'):
        return obj.space_type.name if obj.space_type else '全局'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('user', 'space_type')

    # --- 权限检查方法 ---
    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.view_userpenaltypointspperspacetype')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('bookings.view_userpenaltypointspperspacetype', obj)

    def has_add_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.add_userpenaltypointspperspacetype')

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.change_userpenaltypointspperspacetype', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.delete_userpenaltypointspperspacetype', obj)

# ====================================================================
# SpaceTypeBanPolicy Admin (空间类型禁用策略管理)
# ====================================================================
@admin.register(SpaceTypeBanPolicy)
class SpaceTypeBanPolicyAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'space_type_display', 'threshold_points', 'ban_duration',
        'priority', 'is_active', 'description'
    )
    list_filter = ('is_active', 'space_type', 'priority')
    search_fields = ('description', 'space_type__name')
    raw_id_fields = ('space_type',)

    fieldsets = (
        (None, {
            'fields': ('space_type', ('threshold_points', 'ban_duration'), 'priority', 'is_active', 'description')
        }),
        ('系统信息', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    readonly_fields = ('created_at', 'updated_at')

    ordering = ['space_type_id', 'space_type__name', '-threshold_points', '-priority']    # -------------------------------

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'SpaceTypeBanPolicy'):
        return obj.space_type.name if obj.space_type else '全局'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('space_type')

    # --- 权限检查方法 ---
    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.view_spacetypebanpolicy')

    def has_view_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.view_spacetypebanpolicy', obj)

    def has_add_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.add_spacetypebanpolicy')

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.change_spacetypebanpolicy', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.delete_spacetypebanpolicy', obj)

# ====================================================================
# UserSpaceTypeBan Admin (用户禁用记录管理)
# ====================================================================
@admin.register(UserSpaceTypeBan)
class UserSpaceTypeBanAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user_display', 'space_type_display', 'start_date', 'end_date',
        'is_active', 'ban_policy_applied_display', 'reason', 'issued_by_display', 'issued_at'
    )
    list_filter = ('space_type', 'issued_at', 'user', 'issued_by')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'reason', 'space_type__name')
    date_hierarchy = 'issued_at'
    raw_id_fields = ('user', 'space_type', 'ban_policy_applied', 'issued_by')

    fieldsets = (
        (None, {
            'fields': ('user', 'space_type', ('start_date', 'end_date'), 'reason',)
        }),
        ('策略与记录', {
            'fields': ('ban_policy_applied', ('issued_by', 'issued_at'))
        }),
    )
    readonly_fields = ('issued_at',)  # issued_by 也可以设为只读，如果由系统自动填充

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

    @admin.display(description='是否活跃')
    def is_active(self, obj: 'UserSpaceTypeBan'):
        return obj.end_date > timezone.now()

    is_active.boolean = True  # 显示为勾选框

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('user', 'space_type', 'ban_policy_applied', 'issued_by')

    # --- 权限检查方法 ---
    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.view_userspacetypeban')

    def has_view_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.view_userspacetypeban', obj)

    def has_add_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.add_userspacetypeban')

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.change_userspacetypeban', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.delete_userspacetypeban', obj)


# ====================================================================
# UserSpaceTypeExemption Admin (用户豁免记录管理) - 现在在这里注册
# ====================================================================
@admin.register(UserSpaceTypeExemption)
class UserSpaceTypeExemptionAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user_display', 'space_type_display', 'exemption_reason',
        'start_date', 'end_date', 'is_active', 'granted_by_display', 'granted_at'
    )
    list_filter = ('space_type', 'granted_at', 'user', 'granted_by')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'exemption_reason', 'space_type__name')
    date_hierarchy = 'granted_at'
    raw_id_fields = ('user', 'space_type', 'granted_by')

    fieldsets = (
        (None, {
            'fields': ('user', 'space_type', ('start_date', 'end_date'), 'exemption_reason',)
        }),
        ('授权信息', {
            'fields': (('granted_by', 'granted_at'),)
        }),
    )
    readonly_fields = ('granted_at',)

    @admin.display(description='用户')
    def user_display(self, obj: 'UserSpaceTypeExemption'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'UserSpaceTypeExemption'):
        return obj.space_type.name if obj.space_type else '全局'

    @admin.display(description='授权人员')
    def granted_by_display(self, obj: 'UserSpaceTypeExemption'):
        return obj.granted_by.get_full_name if obj.granted_by else 'N/A'

    @admin.display(description='是否活跃')
    def is_active(self, obj: 'UserSpaceTypeExemption'):
        from django.utils import timezone
        # 如果 start_date 和 end_date 都为 None，表示永久活跃
        if obj.start_date is None and obj.end_date is None:
            return True
        # 如果只有 end_date 为 None，且 start_date 在过去或现在
        if obj.end_date is None and obj.start_date is not None and obj.start_date <= timezone.now():
            return True
        # 如果 start_date 和 end_date 都在，检查当前时间是否在范围内
        return obj.start_date <= timezone.now() < obj.end_date if obj.start_date and obj.end_date else False

    is_active.boolean = True

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('user', 'space_type', 'granted_by')

    # --- 权限检查方法 ---
    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.view_userspacetypeexemption')

    def has_view_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.view_userspacetypeexemption', obj)

    def has_add_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.add_userspacetypeexemption')

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.change_userspacetypeexemption', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff and request.user.has_perm('bookings.delete_userspacetypeexemption', obj)