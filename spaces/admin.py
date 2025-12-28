# spaces/admin.py
from django.contrib import admin
from django.contrib import messages
from django.db import transaction
from spaces.models import Amenity, Space

# 导入 Booking 模型 (这里使用更安全的导入方式)
# 如果 bookings/models.py 文件不存在，会引发 ImportError。
# 如果存在，会尝试加载 Booking 模型。
try:
    from bookings.models import Booking


    # 定义一个辅助函数，用于SpaceAdmin中的删除逻辑，以适应Booking可能不存在的情况
    def check_booking_exists_for_space(space_obj):
        return Booking.objects.filter(space=space_obj).exists()
except ImportError:
    # bookings.models.py 或 Booking 模型不存在时，提供一个模拟函数
    print("Warning: bookings.models.Booking could not be imported. Assuming no bookings exist for deletion checks.")


    def check_booking_exists_for_space(space_obj):
        return False  # 总是返回 False，表示没有预订


# ====================================================================
# Amenity Admin (设施管理) - 保持不变
# ====================================================================
@admin.register(Amenity)
class AmenityAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)
    fields = ('name', 'description')

    def has_module_permission(self, request):
        if not request.user.is_staff:
            return False
        return request.user.is_space_manager or request.user.is_admin or request.user.is_super_admin

    def has_change_permission(self, request, obj=None):
        return request.user.is_super_admin or request.user.is_admin

    def has_add_permission(self, request):
        return request.user.is_super_admin or request.user.is_admin

    def has_delete_permission(self, request, obj=None):
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
    date_hierarchy = 'created_at'

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
            'classes': ('collapse',)
        }),
    )

    # 声明所有自定义 actions，包括我们重写的 delete_selected
    actions = [
        'make_spaces_bookable',
        'make_spaces_not_bookable',
        'activate_spaces',
        'deactivate_spaces',
        'require_approval_for_spaces',
        'dont_require_approval_for_spaces',
        'delete_selected',  # 这里直接声明我们的自定义 delete_selected 方法
    ]

    def get_actions(self, request):
        """
        根据用户角色动态显示或隐藏 actions。
        非系统管理员和非超级管理员用户将只能看到 Admin 默认的 '删除' 批量操作（如果被授予权限的话），
        或者我们选择完全隐藏所有批量操作。
        这里我们选择：只有系统管理员和超级管理员能执行我们所有的自定义批量操作。
        """
        actions = []  # 初始化一个空列表，只添加我们明确允许的actions

        # 获取当前用户是否有权限执行批量操作
        can_do_bulk_actions = request.user.is_super_admin or request.user.is_admin

        if can_do_bulk_actions:
            # 添加所有我们自定义的 actions 到 actions 列表中
            # Python 3.6+ 支持字典保持插入顺序，但为了清晰，我们直接构建列表
            for action_name in self.actions:
                # self.get_action(action_name) 会返回 `(func, name, description)`
                # 我们只需要 func 和 description
                func, name, description = self.get_action(action_name)
                # 将元组添加到 actions 列表中
                actions.append((func, name, description))

        # 将 actions 列表转换为字典形式，然后返回
        # 这确保了 actions 列表中只包含用户有权限执行的操作
        # Django Admin 会根据这个字典来渲染 Actions 下拉菜单
        # {'action_name': (function, name, description)}
        return {a[1]: a for a in actions}

    def has_module_permission(self, request):
        if not request.user.is_staff:
            return False
        return request.user.is_space_manager or request.user.is_admin or request.user.is_super_admin

    def has_add_permission(self, request):
        return request.user.is_super_admin or request.user.is_admin

    def has_change_permission(self, request, obj=None):
        return request.user.is_super_admin or request.user.is_admin

    def has_delete_permission(self, request, obj=None):
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
                    space.save()
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

                # 使用导入的辅助函数进行检查
                for space in queryset:
                    if check_booking_exists_for_space(space):  # 使用辅助函数
                        undeletable_names.append(space.name)
                    else:
                        deletable_spaces_ids.append(space.id)

                deleted_count = 0
                if deletable_spaces_ids:
                    deleted_count, _ = Space.objects.filter(id__in=deletable_spaces_ids).delete()

                if undeletable_names:
                    self.message_user(request,
                                      f"以下空间无法删除，因为它们被预订记录引用: {', '.join(undeletable_names)[:200]}...",
                                      messages.WARNING)
                if deleted_count > 0:
                    self.message_user(request, f"成功删除了 {deleted_count} 个空间。", messages.SUCCESS)
                elif not undeletable_names:
                    self.message_user(request, "没有选择任何要删除的空间或它们已经不存在了。", messages.WARNING)

        except Exception as e:
            self.message_user(request, f"批量删除空间失败: {e}", messages.ERROR)