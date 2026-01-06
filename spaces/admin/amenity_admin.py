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
        if not request.user.is_authenticated: return False
        return request.user.is_staff and (
                request.user.is_superuser or request.user.is_system_admin or request.user.is_space_manager)

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin or request.user.is_space_manager

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        # 允许系统管理员、超级管理员 和 空间管理员 添加设施类型
        return request.user.is_superuser or request.user.is_system_admin or request.user.is_space_manager

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin