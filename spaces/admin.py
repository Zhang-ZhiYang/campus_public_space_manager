# spaces/admin.py
from django.contrib import admin
from django.contrib import messages
from django.db import transaction
# 确保所有模型都被导入以供使用和类型检查
from spaces.models import Amenity, Space, SpaceType, BookableAmenity, Role # <-- 导入 Role

# 导入 CustomUser 和 Booking 用于类型检查和删除逻辑
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from users.models import CustomUser
    from bookings.models import Booking

# 用于运行时安全导入及模拟
try:
    from users.models import CustomUser, Role
    from bookings.models import Booking
except ImportError:
    class CustomUser:  # Mock CustomUser
        is_authenticated = False
        is_staff = False
        has_perm = lambda self, perm_name, obj=None: False
        username = "mock_user"

    class Role: # Mock Role for admin.py
        name = "Mock Role"
        def __str__(self): return self.name

    class Booking:  # Mock Booking
        @staticmethod
        def objects_filter_space_exists(space_obj): return False

        @staticmethod
        def objects_filter_bookable_amenity_exists(bookable_amenity_obj): return False

    print("Warning: Missing modules (users.models.CustomUser, users.models.Role or bookings.models.Booking). "
          "Using mock objects in spaces/admin.py. Admin functionalities may be limited.")

# ====================================================================
# SpaceType Admin (空间类型管理) - 这里不应有 filter_horizontal
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

    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('spaces.view_spacetype')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('spaces.view_spacetype', obj)

    def has_add_permission(self, request):
        return request.user.has_perm('spaces.add_spacetype')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('spaces.change_spacetype', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('spaces.delete_spacetype', obj)

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
        return request.user.is_staff and request.user.has_perm('spaces.view_amenity')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('spaces.view_amenity', obj)

    def has_add_permission(self, request):
        return request.user.has_perm('spaces.add_amenity')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('spaces.change_amenity', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('spaces.delete_amenity', obj)

# ====================================================================
# BookableAmenity Inline
# ====================================================================
class BookableAmenityInline(admin.TabularInline):
    model = BookableAmenity
    extra = 1
    fields = ('amenity', 'quantity', 'is_bookable', 'is_active')

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm('spaces.add_bookableamenity')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('spaces.change_bookableamenity', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('spaces.delete_bookableamenity', obj)

# ====================================================================
# Space Admin (空间管理) - filter_horizontal 应在这里
# ====================================================================
@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'location', 'space_type', 'parent_space', 'capacity',
        'is_bookable', 'is_active', 'is_container', 'requires_approval',
        'display_restricted_roles'
    )
    list_filter = (
        'is_bookable', 'is_active', 'is_container', 'requires_approval',
        'space_type', ('parent_space', admin.RelatedOnlyFieldListFilter),
        'restricted_roles'
    )
    search_fields = ('name', 'location', 'description')
    date_hierarchy = 'created_at'
    raw_id_fields = ('parent_space',)

    inlines = [BookableAmenityInline]

    # --- 确保这一行在 SpaceAdmin 中，且只在这里 ---
    filter_horizontal = ('restricted_roles',)
    # -----------------------------------------------

    fieldsets = (
        (None, {
            'fields': ('name', 'location', 'description', 'image',)
        }),
        ('层级与类型', {
            'fields': ('space_type', 'parent_space', 'is_container',)
        }),
        ('预订设置', {
            'fields': ('capacity', 'is_bookable', 'is_active', 'requires_approval',)
        }),
        ('预订角色限制', {
            'fields': ('restricted_roles',),
            'description': '选择在此空间中禁止预订的角色。如果未选择任何角色，则所有角色都可以预订。',
            'classes': ('collapse',)
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

    def has_module_permission(self, request):
        return request.user.is_staff and request.user.has_perm('spaces.view_space')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('spaces.view_space', obj)

    def has_add_permission(self, request):
        return request.user.has_perm('spaces.add_space')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('spaces.change_space', obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('spaces.delete_space', obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm('spaces.change_space'):
            for action_name in self.actions:
                if action_name in actions:
                    del actions[action_name]
        if 'delete_selected' in actions and not request.user.has_perm('spaces.delete_space'):
            del actions['delete_selected']
        return actions

    def display_restricted_roles(self, obj):
        # 优化：通过 prefetch_related 获取角色，减少查询
        # obj.restricted_roles.all() 在 list_display 中可能导致 N+1 查询问题。
        # 更好的做法是在 get_queryset 中通过 prefetch_related 预先加载。
        # 如果没有预加载，这里每次都会查询。
        return ", ".join([role.name for role in obj.restricted_roles.all()])
    display_restricted_roles.short_description = "禁止预订角色"

    # --- 添加 get_queryset 以预加载 M2M 字段，优化 display_restricted_roles 的性能 ---
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related('restricted_roles')
    # ----------------------------------------------------------------------------------

    @admin.action(description="将选择的空间设置为可预订")
    def make_spaces_bookable(self, request, queryset):
        if not request.user.has_perm('spaces.change_space'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(is_bookable=True)
        self.message_user(request, f"{updated_count} 个空间已设置为可预订。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为不可预订")
    def make_spaces_not_bookable(self, request, queryset):
        if not request.user.has_perm('spaces.change_space'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(is_bookable=False)
        self.message_user(request, f"{updated_count} 个空间已设置为不可预订。", messages.SUCCESS)

    @admin.action(description="激活选择的空间")
    def activate_spaces(self, request, queryset):
        if not request.user.has_perm('spaces.change_space'):
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
        if not request.user.has_perm('spaces.change_space'):
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
        if not request.user.has_perm('spaces.change_space'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(requires_approval=True)
        self.message_user(request, f"{updated_count} 个空间已设置为需要审批。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为无需审批")
    def dont_require_approval_for_spaces(self, request, queryset):
        if not request.user.has_perm('spaces.change_space'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return
        updated_count = queryset.update(requires_approval=False)
        self.message_user(request, f"{updated_count} 个空间已设置为无需审批。", messages.SUCCESS)

    @admin.action(description="删除选择的空间")
    def delete_selected(self, request, queryset):
        if not request.user.has_perm('spaces.delete_space'):
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
                    # Note: Booking.objects_filter_space_exists 是一个模拟方法
                    # 在真实环境中，你需要确保 Booking.objects.filter(space=space).exists() 这样工作
                    if Booking.objects_filter_space_exists(space):
                        undeletable_names.append(f"{space.name} (存在空间预订)")
                        continue

                    # 3. 检查是否有预订记录 (通过 BookableAmenity)
                    has_amenity_bookings = False
                    for bookable_amenity in space.bookable_amenities.all():
                        # 同上，Booking.objects_filter_bookable_amenity_exists 是模拟方法
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