# bookings/admin/daily_booking_limit_admin.py

from django.contrib import admin
from django.conf import settings
from bookings.models import DailyBookingLimit
from django.db.models import QuerySet # 导入 QuerySet for type hinting

CustomUser = settings.AUTH_USER_MODEL # 获取 CustomUser 模型

@admin.register(DailyBookingLimit)
class DailyBookingLimitAdmin(admin.ModelAdmin):
    list_display = ('group_name', 'max_bookings', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('group__name',)
    raw_id_fields = ('group',) # 使用 raw_id_fields 便于选择 Group

    fieldsets = (
        (None, {'fields': ('group', 'max_bookings', 'is_active')}),
        ('时间信息', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)})
    )
    readonly_fields = ('created_at', 'updated_at',)

    @admin.display(description='用户组')
    def group_name(self, obj: 'DailyBookingLimit'):
        return obj.group.name if obj.group else 'N/A'

    def has_module_permission(self, request):
        """
        只有超级用户和系统管理员能看到此模块。
        """
        return request.user.is_authenticated and (request.user.is_superuser or getattr(request.user, 'is_system_admin', False))

    def has_view_permission(self, request, obj=None):
        """
        只有超级用户和系统管理员能查看。
        """
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        """
        只有超级用户和系统管理员能添加。
        """
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        """
        只有超级用户和系统管理员能修改。
        """
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        """
        只有超级用户和系统管理员能删除。
        """
        return self.has_module_permission(request)