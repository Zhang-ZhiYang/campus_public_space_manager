from django.contrib import admin


# 如果你使用了 GuardedModelAdmin，请保留这一行
# from guardian.admin import GuardedModelAdmin

class YourModelAdmin(admin.ModelAdmin):  # 或 class YourGuardedModelAdmin(GuardedModelAdmin):
    # ... 其他 Admin 配置，如 list_display, search_fields 等 ...

    def has_module_permission(self, request):
        """
        统一的模块可见性权限检查。
        - 未登录用户：不可见。
        - 超级用户/系统管理员：总是可见。
        - 空间管理员：取决于是否被明确分配了该 मॉडल 的 Django 默认 'view_xxx' 权限。
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

    # ... 其他权限方法，如 get_queryset, has_view_permission 等保持不变 ...
    # 这些方法仍然需要区分 is_superuser, is_system_admin 和 is_space_manager，
    # 并使用 get_objects_for_user 等进行对象级数据过滤。