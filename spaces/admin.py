# spaces/admin.py
from django.contrib import admin
from django.contrib import messages
from django.db import transaction
# 确保所有模型都被导入以供使用和类型检查
from spaces.models import Amenity, Space, SpaceType, BookableAmenity

# 导入 CustomUser 和 Booking 用于权限检查和删除逻辑
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from users.models import CustomUser
    from bookings.models import Booking

# 导入自定义权限辅助函数
from core.utils.admin_permissions import (
    has_global_admin_privileges,
    has_space_management_privileges
)

# 用于运行时安全导入及模拟
try:
    from users.models import CustomUser
    from bookings.models import Booking
except ImportError:
    class CustomUser:
        is_authenticated = False
        is_super_admin = False
        is_admin = False
        is_space_manager = False
        username = "mock_user"


    class Booking:
        @staticmethod
        def objects_filter_space_exists(space_obj): return False

        @staticmethod
        def objects_filter_bookable_amenity_exists(bookable_amenity_obj): return False


    print("Warning: Missing modules (users.models.CustomUser or bookings.models.Booking). "
          "Using mock objects in spaces/admin.py. Admin functionalities may be limited.")


# ====================================================================
# SpaceType Admin (空间类型管理) - 全局管理员权限
# ====================================================================
@admin.register(SpaceType)
class SpaceTypeAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'is_container_type', 'default_is_bookable', 'default_requires_approval',
        'default_available_start_time', 'default_available_end_time',
        'description'
    )
    search_fields = ('name',)
    list_filter = ('is_container_type', 'default_is_bookable', 'default_requires_approval')

    fieldsets = (
        (None, {'fields': ('name', 'description')}),
        ('类型属性', {'fields': ('is_container_type',)}),
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

    def _has_permission(self, request, obj=None):
        # 封装一下，保持代码简洁
        return has_global_admin_privileges(request.user)

    def has_module_permission(self, request):
        return self._has_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_add_permission(self, request):
        return self._has_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._has_permission(request, obj)


# ====================================================================
# Amenity Admin (设施类型管理) - 全局管理员权限
# ====================================================================
@admin.register(Amenity)
class AmenityAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_bookable_individually', 'description')
    search_fields = ('name',)
    list_filter = ('is_bookable_individually',)
    fields = ('name', 'description', 'is_bookable_individually')

    def _has_permission(self, request, obj=None):
        return has_global_admin_privileges(request.user)

    def has_module_permission(self, request):
        return self._has_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_add_permission(self, request):
        return self._has_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._has_permission(request, obj)


# ====================================================================
# BookableAmenity Inline (作为 Space 的内联显示) - 空间管理权限
# ====================================================================
class BookableAmenityInline(admin.TabularInline):
    model = BookableAmenity
    extra = 1
    fields = ('amenity', 'quantity', 'is_bookable', 'is_active')

    def _has_permission(self, request, obj=None):
        return has_space_management_privileges(request.user)

    # Inline 不直接有 has_module_permission 或 has_view_permission
    # 它的可见性和权限通常与其父级 ModelAdmin 绑定
    def has_add_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._has_permission(request, obj)


# ====================================================================
# Space Admin (空间管理) - 空间管理权限
# ====================================================================
@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'location', 'space_type', 'parent_space', 'capacity',
        'is_bookable', 'is_active', 'is_container', 'requires_approval'
    )
    list_filter = (
        'is_bookable', 'is_active', 'is_container', 'requires_approval',
        'space_type', ('parent_space', admin.RelatedOnlyFieldListFilter)
    )
    search_fields = ('name', 'location', 'description')
    date_hierarchy = 'created_at'
    # 修复：raw_id_fields 应该用于外键而非 ManyToManyField，且名称要与模型字段匹配
    raw_id_fields = ('parent_space',)  # SpaceType 是外键，但通常以选择框形式更好

    inlines = [BookableAmenityInline]

    fieldsets = (
        (None, {
            'fields': ('name', 'location', 'description', 'image',)
        }),
        ('层级与类型', {
            'fields': ('space_type', 'parent_space', 'is_container',)  # 引用正确字段
        }),
        ('预订设置', {
            # 修复：Space 模型上没有直接的 'amenities' 字段
            'fields': ('capacity', 'is_bookable', 'is_active', 'requires_approval',)
        }),
        ('时间与时长规则', {
            'fields': (
                'available_start_time', 'available_end_time',
                'min_booking_duration', 'max_booking_duration',
                'buffer_time_minutes'
            ),
            'classes': ('collapse',)
        }),
    )

    actions = [
        'make_spaces_bookable', 'make_spaces_not_bookable',
        'activate_spaces', 'deactivate_spaces',
        'require_approval_for_spaces', 'dont_require_approval_for_spaces',
        'delete_selected',
    ]

    def _has_permission(self, request, obj=None):
        return has_space_management_privileges(request.user)

    # 权限检查方法
    def has_module_permission(self, request):
        return self._has_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_add_permission(self, request):
        return self._has_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._has_permission(request, obj)

    # 重写 get_actions 确保只有有权限的用户能看到并执行自定义动作
    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self._has_permission(request):  # 无权限用户直接返回空动作
            return {}

        # 否则，返回所有定义的 actions
        # 注意：此处不再复杂筛选，因为_has_permission已经控制了操作者的权限。
        # 如果需要更细粒度的控制某个Action，则在该Action内部再做判断。
        return actions

    # --- Action 方法定义 --- (确保所有 Action 方法内部也使用统一的权限检查)

    @admin.action(description="将选择的空间设置为可预订")
    def make_spaces_bookable(self, request, queryset):
        if not self._has_permission(request):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(is_bookable=True)
        self.message_user(request, f"{updated_count} 个空间已设置为可预订。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为不可预订")
    def make_spaces_not_bookable(self, request, queryset):
        if not self._has_permission(request):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(is_bookable=False)
        self.message_user(request, f"{updated_count} 个空间已设置为不可预订。", messages.SUCCESS)

    @admin.action(description="激活选择的空间")
    def activate_spaces(self, request, queryset):
        if not self._has_permission(request):
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
        if not self._has_permission(request):
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
        if not self._has_permission(request):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(requires_approval=True)
        self.message_user(request, f"{updated_count} 个空间已设置为需要审批。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为无需审批")
    def dont_require_approval_for_spaces(self, request, queryset):
        if not self._has_permission(request):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(requires_approval=False)
        self.message_user(request, f"{updated_count} 个空间已设置为无需审批。", messages.SUCCESS)

    @admin.action(description="删除选择的空间")
    def delete_selected(self, request, queryset):
        if not self._has_permission(request):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return

        try:
            with transaction.atomic():
                deletable_spaces_ids = []
                undeletable_names = []

                for space in queryset:
                    # 1. 检查是否有子空间
                    if space.child_spaces.exists():
                        undeletable_names.append(f"{space.name} (存在子空间)")
                        continue

                    # 2. 检查是否有预订记录 (空间本身)
                    if Booking.objects_filter_space_exists(space):
                        undeletable_names.append(f"{space.name} (存在空间预订)")
                        continue

                    # 3. 检查是否有预订记录 (通过 BookableAmenity)
                    has_amenity_bookings = False
                    for bookable_amenity in space.bookable_amenities.all():
                        if Booking.objects_filter_bookable_amenity_exists(bookable_amenity):
                            has_amenity_bookings = True
                            break
                    if has_amenity_bookings:
                        undeletable_names.append(f"{space.name} (存在设施预订)")
                        continue

                    deletable_spaces_ids.append(space.id)

                deleted_count = 0
                if deletable_spaces_ids:
                    deleted_count, _ = Space.objects.filter(id__in=deletable_spaces_ids).delete()

                if undeletable_names:
                    self.message_user(request,
                                      f"以下空间无法删除: {', '.join(undeletable_names)[:500]}...",
                                      messages.WARNING)
                if deleted_count > 0:
                    self.message_user(request, f"成功删除了 {deleted_count} 个空间。", messages.SUCCESS)

        except Exception as e:
            self.message_user(request, f"批量删除空间失败: {e}", messages.ERROR)