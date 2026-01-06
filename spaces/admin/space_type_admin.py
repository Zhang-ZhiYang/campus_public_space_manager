# spaces/admin/space_type_admin.py
from django.contrib import admin
# 导入本应用的模型
from spaces.models import SpaceType

# ====================================================================
# SpaceType Admin (空间类型管理)
# ====================================================================
@admin.register(SpaceType)
class SpaceTypeAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'is_container_type', 'is_basic_infrastructure', 'default_is_bookable', 'default_requires_approval',
        'default_available_start_time', 'default_available_end_time',
        'description'
    )
    search_fields = ('name',)
    list_filter = ('is_container_type', 'is_basic_infrastructure', 'default_is_bookable', 'default_requires_approval')

    fieldsets = (
        (None, {'fields': ('name', 'description')}),
        ('类型属性', {'fields': ('is_container_type', 'is_basic_infrastructure')}),
        ('默认预订规则 (创建空间时可作为默认值)', {
            'fields': (
                'default_is_bookable', 'default_requires_approval',
                'default_available_start_time', 'default_available_end_time',
                'default_min_booking_duration', 'default_max_booking_duration',
                'default_buffer_time_minutes'
            ),
            'classes': ('collapse',)
        }),
    )

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_staff and (request.user.is_superuser or request.user.is_system_admin)

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
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