# spaces/admin.py
from django.contrib import admin
from django.contrib import messages
from django.db import transaction
from spaces.models import Amenity, Space


# ====================================================================
# Amenity Admin (设施管理)
# ====================================================================
@admin.register(Amenity)
class AmenityAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)
    fields = ('name', 'description')

    def has_change_permission(self, request, obj=None):
        """只有系统管理员或超级管理员可以修改设施"""
        return request.user.is_super_admin or request.user.is_admin

    def has_add_permission(self, request):
        """只有系统管理员或超级管理员可以添加设施"""
        return request.user.is_super_admin or request.user.is_admin

    def has_delete_permission(self, request, obj=None):
        """只有系统管理员或超级管理员可以删除设施"""
        return request.user.is_super_admin or request.user.is_admin


# ====================================================================
# Space Admin (空间管理)
# ====================================================================
@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    list_display = (
    'name', 'location', 'capacity', 'is_bookable', 'is_active', 'requires_approval', 'created_at', 'updated_at')
    list_filter = ('is_bookable', 'is_active', 'requires_approval')
    search_fields = ('name', 'location', 'description')
    raw_id_fields = ('amenities',)  # 如果设施很多，可以用这个，否则用 ManyToManyField_widget
    date_hierarchy = 'created_at'  # 按日期进行筛选

    # 定义表单字段的顺序和分组
    fieldsets = (
        (None, {
            'fields': ('name', 'location', 'description', 'image',)
        }),
        ('预订设置', {
            'fields': ('capacity', 'is_bookable', 'is_active', 'requires_approval', 'amenities')
        }),
        ('时间与时长', {
            'fields': ('available_start_time', 'available_end_time', 'min_booking_duration', 'max_booking_duration',
                       'buffer_time_minutes'),
            'classes': ('collapse',)  # 可以折叠这个部分
        }),
    )

    # ================================================================
    # 自定义 Actions
    # ================================================================
    actions = [
        'make_spaces_bookable',  # 批量设置为可预订
        'make_spaces_not_bookable',  # 批量设置为不可预订
        'activate_spaces',  # 批量激活空间
        'deactivate_spaces',  # 批量停用空间
        'require_approval_for_spaces',  # 批量设置需审批
        'dont_require_approval_for_spaces',  # 批量设置无需审批
        'delete_selected',  # 使用自定义的删除方法
    ]

    def get_actions(self, request):
        """
        根据用户角色动态显示或隐藏 actions。
        只有系统管理员和超级管理员能执行这些批量操作。
        """
        actions = super().get_actions(request)

        # 移除默认的 'delete_selected'，改用我们自定义的
        if 'delete_selected' in actions:
            del actions['delete_selected']

        if not (request.user.is_super_admin or request.user.is_admin):
            # 如果不是系统管理员或超级管理员，移除所有自定义批量操作
            for action_name in self.actions:
                if action_name in actions:
                    del actions[action_name]
        else:
            # 如果是系统管理员或超级管理员，则将自定义的删除操作添加到 actions
            if 'delete_selected' not in actions:  # 再次防止重复添加
                actions['delete_selected'] = self.admin_site.get_action('delete_selected')(self.model)
                actions['delete_selected'][0] = 'delete_selected'  # 覆盖默认的名称

        return actions

    def has_add_permission(self, request):
        """只有系统管理员或超级管理员可以添加空间"""
        return request.user.is_super_admin or request.user.is_admin

    def has_change_permission(self, request, obj=None):
        """只有系统管理员或超级管理员可以修改空间"""
        return request.user.is_super_admin or request.user.is_admin

    def has_delete_permission(self, request, obj=None):
        """只有系统管理员或超级管理员可以删除空间"""
        return request.user.is_super_admin or request.user.is_admin

    # --- Action 方法定义 ---

    @admin.action(description="将选择的空间设置为可预订")
    def make_spaces_bookable(self, request, queryset):
        if not (request.user.is_super_admin or request.user.is_admin):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(is_bookable=True)
        self.message_user(request, f"{updated_count} 个空间已设置为可预订。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为不可预订")
    def make_spaces_not_bookable(self, request, queryset):
        if not (request.user.is_super_admin or request.user.is_admin):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(is_bookable=False)
        self.message_user(request, f"{updated_count} 个空间已设置为不可预订。", messages.SUCCESS)

    @admin.action(description="激活选择的空间")
    def activate_spaces(self, request, queryset):
        if not (request.user.is_super_admin or request.user.is_admin):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        try:
            with transaction.atomic():
                updated_count = 0
                for space in queryset:
                    space.is_active = True
                    space.save()
                    updated_count += 1
            self.message_user(request, f"{updated_count} 个空间已激活。", messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"激活空间失败: {e}", messages.ERROR)

    @admin.action(description="停用选择的空间 (同时设为不可预订)")
    def deactivate_spaces(self, request, queryset):
        if not (request.user.is_super_admin or request.user.is_admin):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        try:
            with transaction.atomic():
                updated_count = 0
                for space in queryset:
                    space.is_active = False
                    space.save()  # 调用 save() 会触发自定义 clean() 和保存逻辑
                    updated_count += 1
            self.message_user(request, f"{updated_count} 个空间已停用，并设为不可预订。", messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"停用空间失败: {e}", messages.ERROR)

    @admin.action(description="将选择的空间设置为需要审批")
    def require_approval_for_spaces(self, request, queryset):
        if not (request.user.is_super_admin or request.user.is_admin):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(requires_approval=True)
        self.message_user(request, f"{updated_count} 个空间已设置为需要审批。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为无需审批")
    def dont_require_approval_for_spaces(self, request, queryset):
        if not (request.user.is_super_admin or request.user.is_admin):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(requires_approval=False)
        self.message_user(request, f"{updated_count} 个空间已设置为无需审批。", messages.SUCCESS)

    @admin.action(description="删除选择的空间")
    def delete_selected(self, request, queryset):
        if not (request.user.is_super_admin or request.user.is_admin):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return

        try:
            with transaction.atomic():
                deletable_spaces_ids = []
                undeletable_names = []

                # 导入 Booking 模型进行关联检查
                from bookings.models import Booking  # 仅在需要时导入，避免循环依赖

                for space in queryset:
                    if Booking.objects.filter(space=space).exists():
                        undeletable_names.append(space.name)
                    else:
                        deletable_spaces_ids.append(space.id)

                deleted_count = 0
                if deletable_spaces_ids:
                    # 使用 filter().delete() 效率更高
                    deleted_count, _ = Space.objects.filter(id__in=deletable_spaces_ids).delete()

                if undeletable_names:
                    self.message_user(request,
                                      f"以下空间无法删除，因为它们被预订记录引用: {', '.join(undeletable_names)[:200]}...",
                                      # 限制长度避免消息过长
                                      messages.WARNING)
                if deleted_count > 0:
                    self.message_user(request, f"成功删除了 {deleted_count} 个空间。", messages.SUCCESS)
                elif not undeletable_names:  # 如果没有不可删除的，且也没有删除成功的
                    self.message_user(request, "没有选择任何要删除的空间或它们已经不存在了。", messages.WARNING)

        except Exception as e:
            self.message_user(request, f"批量删除空间失败: {e}", messages.ERROR)