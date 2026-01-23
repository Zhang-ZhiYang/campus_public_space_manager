# spaces/admin/space_admin.py
import logging

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction, models
from django.contrib.auth.models import Group
from django.forms.models import BaseInlineFormSet
from django.db.models import Q

from spaces.models import Amenity, Space, SpaceType, BookableAmenity, \
    SPACE_MANAGEMENT_PERMISSIONS, BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS

from django.conf import settings
from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user, get_perms_for_model, assign_perm

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.html import format_html

CustomUser = get_user_model()
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------
# 用于运行时安全导入及模拟其他应用的模型
# --------------------------------------------------------------------
BOOKINGS_MODELS_LOADED = False
try:
    from bookings.models import Booking

    BOOKINGS_MODELS_LOADED = True
except ImportError:
    class MockQuerySet(models.QuerySet):
        def none(self): return self

        def filter(self, *args, **kwargs): return self

        def values_list(self, *args, **kwargs): return []

    class MockManager(models.Manager):
        def get_queryset(self):
            return MockQuerySet(self.model, using=self._db)

    class MockBooking(models.Model):
        objects = MockManager()

        @staticmethod
        def objects_filter_space_exists(space_obj): return False

        @staticmethod
        def objects_filter_bookable_amenity_exists(bookable_amenity_obj): return False

    Booking = MockBooking

# ====================================================================
# BookableAmenity Inline (保持不变)
# ====================================================================
class BookableAmenityInline(admin.TabularInline):
    model = BookableAmenity
    extra = 0
    fields = ('amenity', 'quantity', 'is_bookable', 'is_active')
    autocomplete_fields = ['amenity']
    verbose_name = "空间设施实例"
    verbose_name_plural = "空间设施实例管理"

    def get_queryset(self, request):
        return super().get_queryset(request)

    def has_add_permission(self, request, obj=None):
        return True

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return True

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

# ====================================================================
# Space Admin (空间管理)
# ====================================================================
@admin.register(Space)
class SpaceAdmin(GuardedModelAdmin):
    list_display = (
        'name', 'location', 'space_type_name', 'parent_space_name', 'capacity',
        'is_bookable', 'is_active', 'is_container', 'requires_approval',
        'check_in_method', # <--- 新增
        'display_permitted_groups', 'managed_by_display'
    )
    list_filter = (
        'is_bookable', 'is_active', 'is_container', 'requires_approval',
        'check_in_method', # <--- 新增
        'space_type',
        ('parent_space', admin.RelatedOnlyFieldListFilter),
        'permitted_groups',
        'managed_by'
    )
    search_fields = ('name', 'location', 'description', 'space_type__name', 'parent_space__name',
                     'managed_by__username', 'managed_by__first_name', 'managed_by__last_name')
    date_hierarchy = 'created_at'
    raw_id_fields = ('parent_space',)
    inlines = [BookableAmenityInline]
    filter_horizontal = ('permitted_groups',)
    readonly_fields = ('created_at', 'updated_at')

    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            (None, {'fields': ('name', 'location', 'description', 'image',)}),
            ('层级与类型', {'fields': ('space_type', 'parent_space', 'is_container',)}),
            ('行为设置', { # <--- 更改为更通用的名称
                'fields': ('capacity', 'is_bookable', 'is_active', 'requires_approval', 'check_in_method')} # <--- 新增 check_in_method
            ),
            ('可预订用户组 (白名单)', {'fields': ('permitted_groups',),
                                       'description': '如果空间非基础型基础设施，则只有选择的用户组可以预订此空间。若为空，则除管理员、空间经理和基础型之外，该空间对非管理员用户不可访问。',
                                       'classes': ('collapse',)}),
            ('管理人员',
             {'fields': ('managed_by',),
              'description': '指定负责管理此空间的主要人员。该人员将获得此空间的管理权限。', }),
            ('时间与时长规则', {
                'fields': ('available_start_time', 'available_end_time', 'min_booking_duration', 'max_booking_duration',
                           'buffer_time_minutes'), 'classes': ('collapse',)}),
            ('时间戳', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse', 'readonly')})
        ]

        if not (request.user.is_superuser or getattr(request.user, 'is_system_admin', False)):
            modified_fieldsets = []
            for fs_name, fs_options in fieldsets:
                if fs_name == '管理人员':
                    if obj is None and getattr(request.user, 'is_space_manager', False):
                        continue
                    else:
                        modified_fieldsets.append((fs_name, fs_options))
                else:
                    modified_fieldsets.append((fs_name, fs_options))
            fieldsets = modified_fieldsets
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        readonly_fields.extend(['created_at', 'updated_at']) # 确保这些字段总是只读

        if not (request.user.is_superuser or getattr(request.user, 'is_system_admin', False)):
            # 如果当前用户是空间管理员且不是系统管理员
            # 并且不是在新建空间时 (obj is not None)，则 managed_by 字段只读
            if obj is not None and ('managed_by' not in readonly_fields): # 仅当是编辑现有对象时
                readonly_fields.append('managed_by')
        return readonly_fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_authenticated:
            kwargs["queryset"] = CustomUser.objects.none()
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        if db_field.name == "managed_by":
            if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
                try:
                    space_manager_group = Group.objects.get(name='空间管理员')
                    kwargs["queryset"] = CustomUser.objects.filter(
                        Q(groups=space_manager_group) | Q(is_superuser=True) | Q(is_staff=True)
                    ).filter(is_active=True).distinct().order_by('username')
                except Group.DoesNotExist:
                    messages.warning(request, "‘空间管理员’用户组不存在，请检查配置。")
                    kwargs["queryset"] = CustomUser.objects.filter(is_active=True).order_by('username')
            else:
                kwargs["queryset"] = CustomUser.objects.filter(pk=request.user.pk, is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change and not obj.managed_by:
            if getattr(request.user, 'is_space_manager', False) or \
                    request.user.is_superuser or \
                    getattr(request.user, 'is_system_admin', False):
                obj.managed_by = request.user

        super().save_model(request, obj, form, change)

    @admin.display(description='管理人员')
    def managed_by_display(self, obj: Space):
        return str(obj.managed_by) if obj.managed_by else 'N/A'

    @admin.display(description='空间类型')
    def space_type_name(self, obj: 'Space'):
        return obj.space_type.name if obj.space_type else 'N/A'

    @admin.display(description='父级空间')
    def parent_space_name(self, obj: 'Space'):
        return obj.parent_space.name if obj.parent_space else 'N/A'

    @admin.display(description="可预订用户组")
    def display_permitted_groups(self, obj: 'Space'):
        return ", ".join([group.name for group in obj.permitted_groups.all()]) if obj.permitted_groups.exists() else "无"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return qs

        return get_objects_for_user(request.user, ['spaces.can_view_space', 'spaces.can_edit_space_info'],
                                    klass=qs).distinct()

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True

        if getattr(request.user, 'is_space_manager', False):
            app_label = self.opts.app_label
            model_name = self.opts.model_name
            permission_codename = f'{app_label}.view_{model_name}'
            return request.user.has_perm(permission_codename)

        return False

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True

        if obj is None: return self.has_module_permission(request)

        return request.user.has_perm('spaces.can_view_space', obj)

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        if getattr(request.user, 'is_space_manager', False):
            return request.user.has_perm('spaces.can_create_space')
        return False

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True

        if obj is None:
            return get_objects_for_user(request.user,
                                        ['spaces.can_edit_space_info', 'spaces.can_change_space_status',
                                         'spaces.can_configure_booking_rules', 'spaces.can_manage_permitted_groups'],
                                        klass=Space).exists()

        return request.user.has_perm('spaces.can_edit_space_info', obj) or \
            request.user.has_perm('spaces.can_change_space_status', obj) or \
            request.user.has_perm('spaces.can_configure_booking_rules', obj) or \
            request.user.has_perm('spaces.can_manage_permitted_groups', obj)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True

        if obj is None:
            return get_objects_for_user(request.user, 'spaces.can_delete_space', klass=Space).exists()

        return request.user.has_perm('spaces.can_delete_space', obj)

    actions = [
        'make_spaces_bookable', 'make_spaces_not_bookable',
        'activate_spaces', 'deactivate_spaces',
        'require_approval_for_spaces', 'dont_require_approval_for_spaces',
        'safer_delete_selected',
    ]

    def get_actions(self, request):
        if not request.user.is_authenticated: return {}
        actions = super().get_actions(request)

        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            if 'delete_selected' in actions: del actions['delete_selected']
            return actions

        allowed_actions_for_space_manager = [
            'make_spaces_bookable', 'make_spaces_not_bookable',
            'activate_spaces', 'deactivate_spaces',
            'require_approval_for_spaces', 'dont_require_approval_for_spaces',
        ]

        actions.pop('delete_selected', None)
        actions.pop('safer_delete_selected', None)

        current_action_names = list(actions.keys())
        for action_name in current_action_names:
            if action_name not in allowed_actions_for_space_manager:
                actions.pop(action_name, None)
        return actions

    @admin.action(description="将选择的空间设置为可预订")
    def make_spaces_bookable(self, request, queryset):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        authorized_queryset_pks = []
        for space in queryset:
            if request.user.is_superuser or getattr(request.user, 'is_system_admin', False) or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_change_space_status', space)):
                authorized_queryset_pks.append(space.pk)
            else:
                messages.error(request, f"您没有权限修改空间 {space.name} 的预订状态。")

        if not authorized_queryset_pks:
            messages.error(request, "没有可选定的空间进行此操作。");
            return

        updated_count = queryset.filter(pk__in=authorized_queryset_pks).update(is_bookable=True)
        self.message_user(request, f"成功将 {updated_count} 个空间设置为可预订。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为不可预订")
    def make_spaces_not_bookable(self, request, queryset):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        authorized_queryset_pks = []
        for space in queryset:
            if request.user.is_superuser or getattr(request.user, 'is_system_admin', False) or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_change_space_status', space)):
                authorized_queryset_pks.append(space.pk)
            else:
                messages.error(request, f"您没有权限修改空间 {space.name} 的预订状态。")

        if not authorized_queryset_pks:
            messages.error(request, "没有可选定的空间进行此操作。");
            return

        updated_count = queryset.filter(pk__in=authorized_queryset_pks).update(is_bookable=False)
        self.message_user(request, f"成功将 {updated_count} 个空间设置为不可预订。", messages.SUCCESS)

    @admin.action(description="激活选择的空间")
    def activate_spaces(self, request, queryset):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        authorized_queryset_pks = []
        for space in queryset:
            if request.user.is_superuser or getattr(request.user, 'is_system_admin', False) or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_change_space_status', space)):
                authorized_queryset_pks.append(space.pk)
            else:
                messages.error(request, f"您没有权限激活空间 {space.name}。")

        if not authorized_queryset_pks:
            messages.error(request, "没有可选定的空间进行此操作。");
            return

        try:
            with transaction.atomic():
                updated_count = 0
                for space in queryset.filter(pk__in=authorized_queryset_pks):
                    if not space.is_active:
                        space.is_active = True
                        space.save(update_fields=['is_active'])
                        updated_count += 1
            self.message_user(request, f"成功激活了 {updated_count} 个空间。", messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"激活空间失败: {e}", messages.ERROR)

    @admin.action(description="停用选择的空间 (同时设为不可预订)")
    def deactivate_spaces(self, request, queryset):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        authorized_queryset_pks = []
        for space in queryset:
            if request.user.is_superuser or getattr(request.user, 'is_system_admin', False) or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_change_space_status', space)):
                authorized_queryset_pks.append(space.pk)
            else:
                messages.error(request, f"您没有权限停用空间 {space.name}。")

        if not authorized_queryset_pks:
            messages.error(request, "没有可选定的空间进行此操作。");
            return

        try:
            with transaction.atomic():
                updated_count = 0
                for space in queryset.filter(pk__in=authorized_queryset_pks):
                    if space.is_active:
                        space.is_active = False
                        space.is_bookable = False
                        space.save(update_fields=['is_active', 'is_bookable'])
                        updated_count += 1
            self.message_user(request, f"成功停用 {updated_count} 个空间，并设为不可预订。", messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"停用空间失败: {e}", messages.ERROR)

    @admin.action(description="将选择的空间设置为需要审批")
    def require_approval_for_spaces(self, request, queryset):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        authorized_queryset_pks = []
        for space in queryset:
            if request.user.is_superuser or getattr(request.user, 'is_system_admin', False) or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_configure_booking_rules', space)):
                authorized_queryset_pks.append(space.pk)
            else:
                messages.error(request, f"您没有权限修改空间 {space.name} 的审批需求。")

        if not authorized_queryset_pks:
            messages.error(request, "没有可选定的空间进行此操作。");
            return

        updated_count = queryset.filter(pk__in=authorized_queryset_pks).update(requires_approval=True)
        self.message_user(request, f"成功将 {updated_count} 个空间设置为需要审批。", messages.SUCCESS)

    @admin.action(description="将选择的空间设置为无需审批")
    def dont_require_approval_for_spaces(self, request, queryset):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        authorized_queryset_pks = []
        for space in queryset:
            if request.user.is_superuser or getattr(request.user, 'is_system_admin', False) or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_configure_booking_rules', space)):
                authorized_queryset_pks.append(space.pk)
            else:
                messages.error(request, f"您没有权限修改空间 {space.name} 的审批需求。")

        if not authorized_queryset_pks:
            messages.error(request, "没有可选定的空间进行此操作。");
            return

        updated_count = queryset.filter(pk__in=authorized_queryset_pks).update(requires_approval=False)
        self.message_user(request, f"成功将 {updated_count} 个空间设置为无需审批。", messages.SUCCESS)

    @admin.action(description="安全删除选择的空间")
    def safer_delete_selected(self, request, queryset):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        if not (request.user.is_superuser or getattr(request.user, 'is_system_admin', False)):
            self.message_user(request, "您没有权限执行此操作。", messages.ERROR);
            return

        if not BOOKINGS_MODELS_LOADED:
            self.message_user(request, "无法加载 Booking 模型，依赖检查将跳过预订记录。", messages.WARNING)
            deletable_spaces_pks = []
            for space in queryset:
                if request.user.has_perm('spaces.can_delete_space', space):
                    deletable_spaces_pks.append(space.pk)
            deleted_count, _ = Space.objects.filter(pk__in=deletable_spaces_pks).delete()
            self.message_user(request, f"由于 Booking 模型未加载，仅按权限成功删除了 {deleted_count} 个空间。",
                              messages.SUCCESS)
            return

        try:
            with transaction.atomic():
                deletable_spaces_pks = []
                undeletable_names = []
                for space in queryset:
                    if not request.user.has_perm('spaces.can_delete_space', space):
                        undeletable_names.append(f"{space.name} (您没有权限删除)");
                        continue
                    if space.child_spaces.exists(): undeletable_names.append(f"{space.name} (存在子空间)"); continue
                    if Booking.objects.filter(space=space).exists(): undeletable_names.append(
                        f"{space.name} (存在空间预订)"); continue
                    if hasattr(space, 'bookable_amenities') and space.bookable_amenities.filter(
                            amenity_bookings__isnull=False).exists():
                        undeletable_names.append(f"{space.name} (存在设施预订)");
                        continue
                    deletable_spaces_pks.append(space.pk)

                deleted_count = 0
                if deletable_spaces_pks: deleted_count, _ = Space.objects.filter(pk__in=deletable_spaces_pks).delete()
                if undeletable_names: self.message_user(request,
                                                        f"以下空间无法删除: {', '.join(undeletable_names)[:500]}",
                                                        messages.WARNING)
                if deleted_count > 0: self.message_user(request, f"成功删除了 {deleted_count} 个空间。",
                                                        messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"批量删除空间失败: {e}", messages.ERROR)