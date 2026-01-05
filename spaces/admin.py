# spaces/admin.py
from django.contrib import admin
from django.contrib import messages
from django.db import transaction
from django.contrib.auth.models import Group  # <-- 导入 Group

# 直接导入本应用的模型，不需要模拟
from spaces.models import Amenity, Space, SpaceType, BookableAmenity

# 导入 CustomUser 和 Booking 用于类型检查和删除逻辑
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from users.models import CustomUser
    from bookings.models import Booking

# 用于运行时安全导入及模拟其他应用的模型
try:
    from users.models import CustomUser
    from bookings.models import Booking
except ImportError:
    class CustomUser:  # Mock CustomUser for other apps if `users` is not ready
        is_authenticated = False
        is_staff = False
        has_perm = lambda self, perm_name, obj=None: False
        username = "mock_user"

        def get_full_name(self): return self.username


    class Booking:  # Mock Booking for other apps if `bookings` is not ready
        @staticmethod
        def objects_filter_space_exists(space_obj):
            return False

        @staticmethod
        def objects_filter_bookable_amenity_exists(bookable_amenity_obj):
            return False


    print("Warning: Missing modules (users.models.CustomUser or bookings.models.Booking). "
          "Using mock objects for *external* models in spaces/admin.py. Admin functionalities may be limited.")


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
# Space Admin (空间管理)
# ====================================================================
@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'location', 'space_type_name', 'parent_space_name', 'capacity',
        'is_bookable', 'is_active', 'is_container', 'requires_approval',
        'display_restricted_groups'
    )
    list_filter = (
        'is_bookable', 'is_active', 'is_container', 'requires_approval',
        'space_type',
        ('parent_space', admin.RelatedOnlyFieldListFilter),
        'restricted_groups',
    )
    search_fields = ('name', 'location', 'description', 'space_type__name', 'parent_space__name')
    date_hierarchy = 'created_at'
    raw_id_fields = ('parent_space',)

    inlines = [BookableAmenityInline]

    filter_horizontal = ('restricted_groups',)

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
        ('预订组限制', {
            'fields': ('restricted_groups',),
            'description': '选择在此空间中禁止预订的用户组。如果未选择任何组，则所有组（除被禁用户外）都可以预订。',
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

    @admin.display(description='空间类型')
    def space_type_name(self, obj: 'Space'):
        return obj.space_type.name if obj.space_type else 'N/A'

    @admin.display(description='父级空间')
    def parent_space_name(self, obj: 'Space'):
        return obj.parent_space.name if obj.parent_space else 'N/A'

    @admin.display(description="禁止预订组")
    def display_restricted_groups(self, obj: 'Space'):
        return ", ".join([group.name for group in obj.restricted_groups.all()])

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # 预加载 related_names 可能会导致性能问题，
        # 确保只加载需要在 list_display 或过滤中使用的关系
        return qs.prefetch_related('restricted_groups').select_related('space_type', 'parent_space')

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
                    if not space.is_active:  # 避免重复激活
                        space.is_active = True
                        space.save()  # 调用 save() 触发模型 clean/save 逻辑
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
                    if space.is_active:  # 避免重复停用
                        space.is_active = False
                        space.save()  # 调用 save() 触发模型 clean/save 逻辑
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

    def delete_selected(self, request, queryset):
        if not request.user.has_perm('spaces.delete_space'):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return

        # 在实际删除前，需要加载 Booking 模型来检查依赖
        try:
            from bookings.models import Booking
        except ImportError:
            self.message_user(request, "无法加载 Booking 模型，请确保 bookings 应用已正确配置。", messages.ERROR)
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
                    if Booking.objects.filter(space=space).exists():
                        undeletable_names.append(f"{space.name} (存在空间预订)")
                        continue

                    # 3. 检查是否有预订记录 (通过 BookableAmenity)
                    has_amenity_bookings = False
                    # 需要手动遍历，因为 BookableAmenity 有自己的 FK 到 Space
                    if space.bookable_amenities.filter(amenity_bookings__isnull=False).exists():
                        has_amenity_bookings = True
                    # for bookable_amenity in space.bookable_amenities.all():
                    #     if Booking.objects.filter(bookable_amenity=bookable_amenity).exists():
                    #         has_amenity_bookings = True
                    #         break
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