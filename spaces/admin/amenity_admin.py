# spaces/admin/amenity_admin.py (终极修订版 - 更严格的空间管理员权限控制)
from django.contrib import admin
from django.contrib import messages  # 导入 messages
# 导入本应用的模型
from spaces.models import Amenity, BookableAmenity  # 导入 BookableAmenity
from guardian.shortcuts import get_objects_for_user  # 导入 guardian


# ====================================================================
# Amenity Admin (设施类型管理)
# ====================================================================
@admin.register(Amenity)
class AmenityAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_bookable_individually', 'description')
    search_fields = ('name',)
    list_filter = ('is_bookable_individually',)
    fields = ('name', 'description', 'is_bookable_individually')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return qs

        # 空间管理员现在可以查看所有设施类型
        if getattr(request.user, 'is_space_manager', False):
            return qs # 返回所有设施类型，不进行过滤
        return qs.none() # 其他用户仍不能查看


    def has_module_permission(self, request):
        """
        统一的模块可见性权限检查。
        - 未登录用户：不可见。
        - 超级用户/系统管理员：总是可见。
        - 空间管理员：取决于是否被明确分配了该 Model 的 Django 默认 'view_xxx' 权限。
        - 其他用户：不可见。
        """
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            return True

        # 如果是空间管理员，动态获取当前模型的 app_label 和 model_name
        # 然后检查是否显式分配了该模型的默认 view_xxx 权限。
        if getattr(request.user, 'is_space_manager', False):
            app_label = self.opts.app_label
            model_name = self.opts.model_name
            permission_codename = f'{app_label}.view_{model_name}'
            return request.user.has_perm(permission_codename)

        return False

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        # 对于 SpaceManager，只要它能看到模块（通过 Django 默认 view_amenity 权限），就可以查看任何 Amenity 对象
        if getattr(request.user, 'is_space_manager', False):
            return True # 允许查看任何设施类型的详情页
        return False
    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        # 空间管理员不能添加
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        # 空间管理员不能修改
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        # 空间管理员不能删除
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)