# spaces/admin.py
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.contrib.auth.models import Group  # <-- 导入 Group
from django.forms import ModelChoiceField  # 导入 ModelChoiceField
from django.db.models import Q  # 导入 Q 对象进行复杂查询

# 直接导入本应用的模型
from .models import Amenity, Space, SpaceType, BookableAmenity

from django.conf import settings  # 导入 settings 获取 AUTH_USER_MODEL
from guardian.admin import GuardedModelAdmin  # 导入 GuardedModelAdmin

# 获取 CustomUser 模型
CustomUser = settings.AUTH_USER_MODEL

# --------------------------------------------------------------------
# 用于运行时安全导入及模拟其他应用的模型
# 确保 bookings/models.py 的 mock 对象足够健壮
# --------------------------------------------------------------------
BOOKINGS_MODELS_LOADED = False
try:
    from bookings.models import Booking

    BOOKINGS_MODELS_LOADED = True
except ImportError:
    class MockBookingManager:
        """一个模拟的 Manager，用于在 bookings 模型未加载时提供安全的默认行为。"""

        def filter(self, *args, **kwargs):
            return self  # 允许链式调用，例如 .filter().exists()

        def exists(self):
            return False  # 总是返回 False，表示没有相关预订

        def none(self):  # 提供一个 .none() 方法，返回空列表
            return []


    class Booking:
        objects = MockBookingManager()  # 关键：提供一个健壮的 Mock 对象管理器

        # 保持原来的静态方法，以防其他地方使用了它们
        @staticmethod
        def objects_filter_space_exists(space_obj):
            print("Warning: Using mock Booking objects_filter_space_exists.")
            return False

        @staticmethod
        def objects_filter_bookable_amenity_exists(bookable_amenity_obj):
            print("Warning: Using mock Booking objects_filter_bookable_amenity_exists.")
            return False


    print(
        "Warning: Missing modules from 'bookings' app. Using robust mock Booking objects in spaces/admin.py. Functionality may be limited.")


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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_staff and (
                request.user.is_system_admin or request.user.has_perm('spaces.view_spacetype'))

    def has_view_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.has_perm('spaces.view_spacetype', obj)

    def has_add_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.has_perm('spaces.add_spacetype')

    def has_change_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.has_perm('spaces.change_spacetype', obj)

    def has_delete_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.has_perm('spaces.delete_spacetype', obj)


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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_staff and (request.user.is_system_admin or request.user.has_perm('spaces.view_amenity'))

    def has_view_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.has_perm('spaces.view_amenity', obj)

    def has_add_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.has_perm('spaces.add_amenity')

    def has_change_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.has_perm('spaces.change_amenity', obj)

    def has_delete_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.has_perm('spaces.delete_amenity', obj)


# ====================================================================
# BookableAmenity Inline
# ====================================================================
class BookableAmenityInline(admin.TabularInline):
    model = BookableAmenity
    extra = 1
    fields = ('amenity', 'quantity', 'is_bookable', 'is_active')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return qs.none()  # 未认证用户不应看到任何 inline 数据

        if request.user.is_superuser or request.user.is_system_admin:
            return qs
        # 在 inline 中，通常是父对象 (Space) 的权限决定了其子对象 (BookableAmenity) 的可见性
        return qs  # 默认显示所有，但在 action/save 时校验权限

    def has_add_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        # obj 是父级 Space 实例
        if request.user.is_system_admin: return True
        if obj and request.user.has_perm('spaces.can_manage_space_amenities', obj):
            return True
        return False

    def has_change_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_system_admin: return True
        if obj:  # obj 是父级 Space
            return request.user.has_perm('spaces.can_manage_space_amenities', obj)
        return False  # 默认由父级空间权限控制

    def has_delete_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_system_admin: return True
        if obj and request.user.has_perm('spaces.can_manage_space_amenities', obj):
            return True
        return False

    def save_model(self, request, obj, form, change):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            messages.error(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            raise ValidationError('没有权限')  # raise ValidationError 来阻止保存

        # 此方法在 TabularInline 中不被直接调用， 通常由父级 Admin 的 save_formset 收集和保存
        # 权限需要在父级或在 before_save 中进行最终校验
        # 这里的 obj.space 是父级 Space 实例
        if not (request.user.is_system_admin or (
                obj.space and request.user.has_perm('spaces.can_manage_space_amenities', obj.space))):
            messages.error(request, '您没有权限修改此设施实例。')
            raise ValidationError('没有权限')
        super().save_model(request, obj, form, change)


# ====================================================================
# Space Admin (空间管理)
# ====================================================================
@admin.register(Space)
class SpaceAdmin(GuardedModelAdmin):  # 继承 GuardedModelAdmin
    list_display = (
        'name', 'location', 'space_type_name', 'parent_space_name', 'capacity',
        'is_bookable', 'is_active', 'is_container', 'requires_approval',
        'display_restricted_groups', 'managed_by_display'
    )
    list_filter = (
        'is_bookable', 'is_active', 'is_container', 'requires_approval',
        'space_type',
        ('parent_space', admin.RelatedOnlyFieldListFilter),
        'restricted_groups',
        'managed_by'  # 可以按管理人员过滤
    )
    search_fields = ('name', 'location', 'description', 'space_type__name', 'parent_space__name',
                     'managed_by__username', 'managed_by__first_name', 'managed_by__last_name')
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
        ('管理人员', {
            'fields': ('managed_by',),
            'description': '指定负责管理此空间的主要人员。该人员将获得此空间的管理权限。',
        }),
        ('时间与时长规则', {
            'fields': (
                'available_start_time', 'available_end_time',
                'min_booking_duration', 'max_booking_duration',
                'buffer_time_minutes'
            ),
            'classes': ('collapse',)
        }),
        ('时间戳', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse', 'readonly')
        })
    )
    readonly_fields = ('created_at', 'updated_at')

    # 限制 managed_by 只能选择 '空间管理员' Group 的用户
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            # 如果未认证，不应该显示任何用户，或者返回安全默认
            kwargs["queryset"] = CustomUser.objects.none()
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        if db_field.name == "managed_by":
            try:
                # 假设存在 '空间管理员' 这个 Group
                space_manager_group = Group.objects.get(name='空间管理员')
                # 仅选择是 is_active 且属于 '空间管理员' Group 的 CustomUser
                kwargs["queryset"] = CustomUser.objects.filter(groups=space_manager_group, is_active=True).order_by(
                    'username')
            except Group.DoesNotExist:
                # 如果 Group 不存在，允许选择所有用户，但应该提示创建 Group
                messages.warning(request, "‘空间管理员’用户组不存在，请检查配置。")
                kwargs["queryset"] = CustomUser.objects.filter(is_active=True).order_by('username')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description='管理人员')
    def managed_by_display(self, obj: Space):
        return obj.managed_by.get_full_name if obj.managed_by else 'N/A'

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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return qs.none()

        if request.user.is_superuser or request.user.is_system_admin:
            return qs  # 超级管理员/系统管理员看所有

        # 空间管理员只看自己有 'can_manage_space_details' 对象级权限的空间
        return qs.for_user(request.user, 'spaces.can_manage_space_details')  # for_user 过滤显示有权限的对象

    # --- Actions ---
    actions = [
        'make_spaces_bookable', 'make_spaces_not_bookable',
        'activate_spaces', 'deactivate_spaces',
        'require_approval_for_spaces', 'dont_require_approval_for_spaces',
        'safer_delete_selected',  # ⚠️ 直接在这里添加 action 的名称
    ]

    def get_actions(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return {}  # 未认证用户无任何 actions

        actions = super().get_actions(request)
        # 只有有 'change_space' 权限的用户才能看到和使用自定义动作
        if not (request.user.is_superuser or request.user.is_system_admin or request.user.has_perm(
                'spaces.change_space')):
            actions_to_remove = [action for action in self.actions if action in actions]
            for action_name in actions_to_remove:
                del actions[action_name]

        # ⚠️ 移除这一行，因为 safer_delete_selected 已经在 actions 列表中作为字符串指定了
        # actions['safer_delete_selected'] = admin.action(description="安全删除选择的空间")(self.safer_delete_selected)

        # 确保默认的 delete_selected 被移除
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    @admin.action(description="将选择的空间设置为可预订")
    def make_spaces_bookable(self, request, queryset):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        if not (request.user.is_superuser or request.user.is_system_admin):
            # 对于空间管理员，检查他们是否有权修改这些特定空间
            for space in queryset:
                if not request.user.has_perm('spaces.can_manage_space_details', space):
                    messages.error(request, f"您没有权限修改空间 {space.name} 的预订状态。")
                    return  # 阻止后续操作

        updated_count = queryset.update(is_bookable=True)
        self.message_user(request, f"{updated_count} 个空间已设置为可预订。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为不可预订")
    def make_spaces_not_bookable(self, request, queryset):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        if not (request.user.is_superuser or request.user.is_system_admin):
            for space in queryset:
                if not request.user.has_perm('spaces.can_manage_space_details', space):
                    messages.error(request, f"您没有权限修改空间 {space.name} 的预订状态。")
                    return
        updated_count = queryset.update(is_bookable=False)
        self.message_user(request, f"{updated_count} 个空间已设置为不可预订。", messages.SUCCESS)

    @admin.action(description="激活选择的空间")
    def activate_spaces(self, request, queryset):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        if not (request.user.is_superuser or request.user.is_system_admin):
            for space in queryset:
                if not request.user.has_perm('spaces.can_manage_space_details', space):
                    messages.error(request, f"您没有权限激活空间 {space.name}。")
                    return

        try:
            with transaction.atomic():
                updated_count = 0
                for space in queryset:
                    if not space.is_active:
                        space.is_active = True
                        space.save()  # 调用 save() 触发模型 clean/save 逻辑
                        updated_count += 1
            self.message_user(request, f"{updated_count} 个空间已激活。", messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"激活空间失败: {e}", messages.ERROR)

    @admin.action(description="停用选择的空间 (同时设为不可预订)")
    def deactivate_spaces(self, request, queryset):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        if not (request.user.is_superuser or request.user.is_system_admin):
            for space in queryset:
                if not request.user.has_perm('spaces.can_manage_space_details', space):
                    messages.error(request, f"您没有权限停用空间 {space.name}。")
                    return
        try:
            with transaction.atomic():
                updated_count = 0
                for space in queryset:
                    if space.is_active:
                        space.is_active = False
                        space.save()  # 调用 save() 触发模型 clean/save 逻辑
                        updated_count += 1
            self.message_user(request, f"{updated_count} 个空间已停用，并设为不可预订。", messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"停用空间失败: {e}", messages.ERROR)

    @admin.action(description="将选择的空间设置为需要审批")
    def require_approval_for_spaces(self, request, queryset):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        if not (request.user.is_superuser or request.user.is_system_admin):
            for space in queryset:
                if not request.user.has_perm('spaces.can_manage_space_details', space):
                    messages.error(request, f"您没有权限修改空间 {space.name} 的审批需求。")
                    return
        updated_count = queryset.update(requires_approval=True)
        self.message_user(request, f"{updated_count} 个空间已设置为需要审批。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为无需审批")
    def dont_require_approval_for_spaces(self, request, queryset):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        if not (request.user.is_superuser or request.user.is_system_admin):
            for space in queryset:
                if not request.user.has_perm('spaces.can_manage_space_details', space):
                    messages.error(request, f"您没有权限修改空间 {space.name} 的审批需求。")
                    return
        updated_count = queryset.update(requires_approval=False)
        self.message_user(request, f"{updated_count} 个空间已设置为无需审批。", messages.SUCCESS)

    @admin.action(description="安全删除选择的空间")
    def safer_delete_selected(self, request, queryset):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        # 检查删除权限
        if not (request.user.is_superuser or request.user.is_system_admin or request.user.has_perm(
                'spaces.delete_space')):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR)
            return

        # 检查 bookings 模型是否成功加载
        if not BOOKINGS_MODELS_LOADED:
            self.message_user(request, "无法加载 Booking 模型，依赖检查将跳过预订记录。请确保 bookings 应用已正确配置。",
                              messages.WARNING)
            # 在没有 Booking 模型时，假定没有预订记录可以删除，只按权限删除
            deletable_spaces_ids = []
            undeletable_names = []
            for space in queryset:
                if request.user.is_superuser or request.user.is_system_admin or request.user.has_perm(
                        'spaces.delete_space', space):
                    deletable_spaces_ids.append(space.id)
                else:
                    undeletable_names.append(f"{space.name} (您没有权限删除)")

            if deletable_spaces_ids:
                deleted_count, _ = Space.objects.filter(id__in=deletable_spaces_ids).delete()
                self.message_user(request, f"由于 Booking 模型未加载，仅按权限成功删除了 {deleted_count} 个空间。",
                                  messages.SUCCESS)
            if undeletable_names:
                self.message_user(request, f"以下空间无法删除: {', '.join(undeletable_names)[:500]}", messages.WARNING)
            return

        try:
            with transaction.atomic():
                deletable_spaces_ids = []
                undeletable_names = []

                for space in queryset:
                    # 权限检查：确保有权限删除这个空间
                    if not (request.user.is_superuser or request.user.is_system_admin or request.user.has_perm(
                            'spaces.delete_space', space)):
                        undeletable_names.append(f"{space.name} (您没有权限删除)")
                        continue

                    # 1. 检查是否有子空间
                    if space.child_spaces.exists():
                        undeletable_names.append(f"{space.name} (存在子空间)")
                        continue

                    # 2. 检查是否有预订记录 (空间本身)
                    if Booking.objects.filter(space=space).exists():
                        undeletable_names.append(f"{space.name} (存在空间预订)")
                        continue

                    # 3. 检查是否有预订记录 (通过 BookableAmenity)
                    # 优化：使用 exists()
                    # 确保 space.bookable_amenities 是一个可用的管理器
                    if hasattr(space, 'bookable_amenities') and space.bookable_amenities.filter(
                            amenity_bookings__isnull=False).exists():
                        undeletable_names.append(f"{space.name} (存在设施预订)")
                        continue

                    deletable_spaces_ids.append(space.id)

                deleted_count = 0
                if deletable_spaces_ids:
                    deleted_count, _ = Space.objects.filter(id__in=deletable_spaces_ids).delete()

                if undeletable_names:
                    self.message_user(request,
                                      f"以下空间无法删除: {', '.join(undeletable_names)[:500]}",
                                      messages.WARNING)
                if deleted_count > 0:
                    self.message_user(request, f"成功删除了 {deleted_count} 个空间。", messages.SUCCESS)

        except Exception as e:
            self.message_user(request, f"批量删除空间失败: {e}", messages.ERROR)

    # --- 权限检查方法 (使用 Django Permissions 和 django-guardian) ---
    def has_module_permission(self, request):
        """
        模块级权限：查看 Space 列表页的权限。
        系统管理员和空间管理员 (通过 is_staff 判断) 且至少有查看 Space 的模型级权限
        或有任何 Space 对象的 managing 权限。
        """
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        # 对于空间管理员，如果他们有 is_staff 且有 spaces.view_space 模型权限，或者有任何一个 space 对象的管理权限，就允许看到模块。
        return request.user.is_staff and (request.user.has_perm('spaces.view_space') or \
                                          request.user.has_perm('spaces.can_manage_space_details'))

    def has_view_permission(self, request, obj=None):
        """
        对象/模型级查看权限。
        obj 为 None 时检查模型级权限（能否看到列表），obj 存在时检查对象级权限（能否查看单个对象）。
        """
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:  # 检查模型级权限 (能否看到列表页)
            return request.user.has_perm('spaces.view_space') or request.user.is_space_manager

        # 检查对象级权限 (能否查看单个对象)
        # 空间管理员如果不是对象的managed_by，但有can_manage_space_details权限，也应能查看
        return request.user.has_perm('spaces.can_manage_space_details', obj) or \
            (obj.managed_by == request.user and request.user.is_space_manager)  # 自己管理的空间肯定能看

    def has_add_permission(self, request):
        """只有系统管理员能添加 Space"""
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.has_perm('spaces.add_space')

    def has_change_permission(self, request, obj=None):
        """
        对象/模型级修改权限。
        obj 为 None 时检查模型级权限（能否进入修改页面），obj 存在时检查对象级权限（能否修改单个对象）。
        """
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        # obj is None: 用户尝试进入 change_list (修改列表) 或 change_form (添加新对象)
        if obj is None:
            return request.user.has_perm(
                'spaces.change_space') and request.user.is_space_manager  # 空间管理员拥有模型级 change_space 权限才能操作

        # obj is not None: 检查对象级权限 (能否修改单个对象)
        return request.user.has_perm('spaces.can_manage_space_details', obj)

    def has_delete_permission(self, request, obj=None):
        """
        对象/模型级删除权限。
        obj 为 None 时检查模型级权限（能否看到删除选项），obj 存在时检查对象级权限（能否删除单个对象）。
        """
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:  # 检查模型级权限
            return request.user.has_perm('spaces.delete_space')

        # 检查对象级权限
        return request.user.has_perm('spaces.delete_space', obj)