# bookings/admin/daily_booking_limit_admin.py

from django.contrib import admin
from django.conf import settings
from bookings.models import DailyBookingLimit  # 确保导入 DailyBookingLimit
from django.db.models import QuerySet  # 导入 QuerySet for type hinting
# 从 spaces.models 导入 SpaceType，因为 DailyBookingLimit 引用了它
from spaces.models import SpaceType

CustomUser = settings.AUTH_USER_MODEL  # 获取 CustomUser 模型


@admin.register(DailyBookingLimit)
class DailyBookingLimitAdmin(admin.ModelAdmin):
    # --- 修正点 1 & 6: 更改 list_display，添加 space_type_name 和 priority ---
    list_display = ('group_name', 'space_type_name', 'max_bookings', 'priority', 'is_active', 'created_at',
                    'updated_at')

    # --- 修正点 2: 更改 list_filter，添加 space_type 和 priority ---
    list_filter = ('is_active', 'space_type', 'priority')

    # --- 修正点 3: 更改 search_fields，添加 space_type__name ---
    search_fields = ('group__name', 'space_type__name',)

    # --- 修正点 4: 更改 raw_id_fields，添加 space_type ---
    raw_id_fields = ('group', 'space_type',)  # 使用 raw_id_fields 便于选择 Group 和 SpaceType

    # --- 修正点 5: 更改 fieldsets，添加 space_type 和 priority ---
    fieldsets = (
        (None, {'fields': ('group', 'space_type', 'max_bookings', 'priority', 'is_active')}),
        ('时间信息', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)})
    )
    readonly_fields = ('created_at', 'updated_at',)

    @admin.display(description='用户组')
    def group_name(self, obj: 'DailyBookingLimit'):
        return obj.group.name if obj.group else 'N/A'

    # --- 新增方法 `space_type_name` ---
    @admin.display(description='空间类型')
    def space_type_name(self, obj: 'DailyBookingLimit'):
        return obj.space_type.name if obj.space_type else '全局限制'

    def has_module_permission(self, request):
        """
        只有超级用户和系统管理员能看到此模块。
        """
        return request.user.is_authenticated and (
                    request.user.is_superuser or getattr(request.user, 'is_system_admin', False))

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