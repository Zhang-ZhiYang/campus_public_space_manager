# spaces/admin/space_type_admin.py
from django.contrib import admin
# 导入本应用的模型
from spaces.models import SpaceType

# ====================================================================
# SpaceType Admin (空间类型管理)
# ====================================================================
@admin.register(SpaceType)
class SpaceTypeAdmin(admin.ModelAdmin):
    # --- REMOVED: 'created_at', 'updated_at' ---
    list_display = (
        'name', 'is_basic_infrastructure', 'default_is_bookable', 'default_requires_approval',
        'default_available_start_time', 'default_available_end_time',
        'description'
    )
    search_fields = ('name',)
    list_filter = (
        'is_basic_infrastructure',
        'default_is_bookable', 'default_requires_approval'
    )
    ordering = ('name',)

    fieldsets = (
        (None, {'fields': ('name', 'description')}),
        ('类型属性', {'fields': ('is_basic_infrastructure',)}),
        ('默认预订规则 (创建空间时可作为默认值)', {
            'fields': (
                'default_is_bookable', 'default_requires_approval',
                'default_available_start_time', 'default_available_end_time',
                'default_min_booking_duration', 'default_max_booking_duration',
                'default_buffer_time_minutes'
            ),
            'classes': ('collapse',)
        }),
        # --- REMOVED: Timestamp fieldset ---
        # ('时间戳', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse', 'readonly')})
        # --- END REMOVED ---
    )
    # --- REMOVED: readonly_fields for timestamps ---
    # readonly_fields = ('created_at', 'updated_at')
    # --- END REMOVED ---

    def has_module_permission(self, request):
        # 只要用户有任何关于 SpaceType 的权限，就允许看到模块
        return request.user.has_perm('spaces.can_view_spacetype') or \
               request.user.has_perm('spaces.can_create_spacetype') or \
               request.user.has_perm('spaces.can_edit_spacetype') or \
               request.user.has_perm('spaces.can_delete_spacetype')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('spaces.can_view_spacetype')

    def has_add_permission(self, request):
        return request.user.has_perm('spaces.can_create_spacetype')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('spaces.can_edit_spacetype')

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('spaces.can_delete_spacetype')