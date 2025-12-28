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

# 导入自定义权限辅助函数
from core.utils.admin_permissions import (
    has_booking_management_privileges,
    has_violation_management_privileges
)

# 确保 CustomUser 可用
try:  # 运行时导入
    from users.models import CustomUser
except ImportError:
    class CustomUser:
        is_authenticated = False
        is_super_admin = False
        is_admin = False
        is_space_manager = False
        username = "mock_user"


    print("Warning: users.models.CustomUser could not be imported in bookings/admin.py. "
          "Using mock objects. Admin functionalities will be limited.")


# ====================================================================
# Booking Admin (预订管理) - 预订管理权限
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

    def _has_permission(self, request, obj=None):
        return has_booking_management_privileges(request.user)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self._has_permission(request):
            return {}
        return actions

    # 权限检查方法
    def has_module_permission(self, request):
        return self._has_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_add_permission(self, request):
        return self._has_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    # --- Action 方法定义 ---
    @admin.action(description="批准选择的预订")
    def approve_bookings(self, request, queryset):
        if not self._has_permission(request):
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
        if not self._has_permission(request):
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
        if not self._has_permission(request):
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
        if not self._has_permission(request):  # 这里应是 has_booking_management_privileges
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.filter(status='APPROVED').update(
            status='COMPLETED',
        )
        self.message_user(request, f"成功标记 {updated_count} 条预订为已完成。", messages.SUCCESS)


# ====================================================================
# Violation Admin (违约记录管理) - 违约管理权限
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
        return obj.issued_by.username if obj.issued_by else 'N/A'

    def _has_permission(self, request, obj=None):
        return has_violation_management_privileges(request.user)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self._has_permission(request):
            return {}
        return actions

    # 权限检查方法
    def has_module_permission(self, request):
        return self._has_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_add_permission(self, request):
        return self._has_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._has_permission(request, obj)