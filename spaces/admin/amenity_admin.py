# spaces/admin/amenity_admin.py
from django.contrib import admin
# 导入本应用的模型
from spaces.models import Amenity

# ====================================================================
# Amenity Admin (设施类型管理)
# ====================================================================
@admin.register(Amenity)
class AmenityAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_bookable_individually', 'description')
    search_fields = ('name',)
    list_filter = ('is_bookable_individually',)
    fields = ('name', 'description', 'is_bookable_individually')

    def has_module_permission(self, request):
        # 只要用户有任何关于 Amenity 的权限，就允许看到模块
        return request.user.has_perm('spaces.can_view_amenity') or \
            request.user.has_perm('spaces.can_create_amenity') or \
            request.user.has_perm('spaces.can_edit_amenity') or \
            request.user.has_perm('spaces.can_delete_amenity')
        # 或者更简单的：
        # return request.user.has_perm('spaces.view_amenity') # Django 默认 view 权限
        # 或者如果你自定义的权限，用 request.user.has_perm('spaces.can_view_amenity')
        # 我们这里假设 `can_view_amenity` 是你自定义的查看权限

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('spaces.can_view_amenity')

    def has_add_permission(self, request):
        return request.user.has_perm('spaces.can_create_amenity')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('spaces.can_edit_amenity')

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('spaces.can_delete_amenity')