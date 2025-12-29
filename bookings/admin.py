# bookings/admin.py
from django.contrib import admin
from django.db import transaction
from django.contrib import messages
from django.utils import timezone

# 导入 CustomUser 的类型提示
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from users.models import CustomUser

from bookings.models import Booking, Violation

# 不需要再导入 custom admin permissions functions 了

# 确保 CustomUser 可用
try:  # 运行时导入
    from users.models import CustomUser
except ImportError:
    class CustomUser:
        is_authenticated = False
        is_staff = False  # 必须为False才能模拟无权限
        has_perm = lambda self, perm_name, obj=None: False  # 模拟无权限
        username = "mock_user"

    print("Warning: users.models.CustomUser could not be imported in bookings/admin.py. "
          "Using mock objects. Admin functionalities will be limited.")

# ====================================================================
# Booking Admin (预订管理) - 完全基于 Django 权限
# ====================================================================
@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user_username', 'booking_target_display', 'status', 'booked_quantity',
        'start_time', 'end_time', 'reviewed_by_username', 'requires_approval_display'
    )
    list_filter = (
        'status', 'start_time', 'end_time',
        'space__space_type',
        'space',
        'bookable_amenity__amenity',
        'bookable_amenity',
        'user', 'reviewed_by'
    )
    search_fields = (
        'user__username', 'purpose', 'space__name', 'bookable_amenity__space__name',
        'bookable_amenity__amenity__name'
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
    readonly_fields = ('created_at', 'updated_at', 'reviewed_at')

    actions = ['approve_bookings', 'reject_bookings', 'cancel_bookings', 'mark_completed_bookings']

    @admin.display(description='用户')
    def user_username(self, obj: 'Booking'):
        return obj.user.username if obj.user else 'N/A'

    @admin.display(description='审核者')
    def reviewed_by_username(self, obj: 'Booking'):
        return obj.reviewed_by.username if obj.reviewed_by else 'N/A'

    @admin.display(description='预订目标')
    def booking_target_display(self, obj: 'Booking'):
        if obj.bookable_amenity:
            return f"设施: {obj.bookable_amenity.amenity.name} ({obj.bookable_amenity.space.name})"
        elif obj.space:
            return f"空间: {obj.space.name}"
        return "N/A"

    @admin.display(description='是否需审批')
    def requires_approval_display(self, obj: 'Booking'):
        if obj.space:
            return "是" if obj.space.requires_approval else "否"
        elif obj.bookable_amenity:
            return "是" if obj.bookable_amenity.space.requires_approval else "否"
        return "N/A"

    # 权限检查方法
    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.view_booking')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('bookings.view_booking', obj)

    def has_add_permission(self, request):
        return request.user.has_perm('bookings.add_booking')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('bookings.change_booking', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('bookings.delete_booking', obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm('bookings.change_booking'):
            # 移除所有自定义 actions
            for action_name in self.actions:
                if action_name in actions:
                    del actions[action_name]
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
            reviewed_by=request.user,
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
        updated_count = queryset.filter(status__in=['PENDING', 'APPROVED']).update(
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
        updated_count = queryset.filter(status='APPROVED').update(
            status='COMPLETED',
        )
        self.message_user(request, f"成功标记 {updated_count} 条预订为已完成。", messages.SUCCESS)

# ====================================================================
# Violation Admin (违约记录管理) - 完全基于 Django 权限
# ====================================================================
@admin.register(Violation)
class ViolationAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user_username', 'violation_type', 'penalty_points',
        'issued_at', 'is_resolved', 'issued_by_username'
    )
    list_filter = ('violation_type', 'is_resolved', 'issued_at', 'user', 'issued_by')
    search_fields = ('user__username', 'booking__purpose', 'description')
    date_hierarchy = 'issued_at'
    raw_id_fields = ('user', 'booking', 'issued_by')

    fieldsets = (
        (None, {
            'fields': (('user', 'booking'), 'violation_type', 'description', ('penalty_points', 'is_resolved'))
        }),
        ('记录信息', {
            'fields': (('issued_by', 'issued_at'),)
        }),
    )
    readonly_fields = ('issued_at',)

    @admin.display(description='用户')
    def user_username(self, obj: 'Violation'):
        return obj.user.username if obj.user else 'N/A'

    @admin.display(description='记录人员')
    def issued_by_username(self, obj: 'Violation'):
        # 修正错误：这里应该是 obj.issued_by
        return obj.issued_by.username if obj.issued_by else 'N/A'

    # 权限检查方法
    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('bookings.view_violation')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('bookings.view_violation', obj)

    def has_add_permission(self, request):
        return request.user.has_perm('bookings.add_violation')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('bookings.change_violation', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('bookings.delete_violation', obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        # 这里批量删除动作的权限检查策略可能需要根据具体需求调整。
        # 当前逻辑是：如果没有 'change_violation' 权限就移除所有自定义actions；
        # 然后如果 'delete_selected' 存在但没有 'delete_violation' 权限，再移除它。
        # 这种双重检查可以保留，但理解其意图很重要。
        if not request.user.has_perm('bookings.change_violation'):
            # 移除所有自定义 actions
            for action_name in self.actions:
                if action_name in actions:
                    del actions[action_name]
        if 'delete_selected' in actions and not request.user.has_perm('bookings.delete_violation'):
            del actions['delete_selected']
        return actions