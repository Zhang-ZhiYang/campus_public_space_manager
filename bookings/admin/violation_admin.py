# bookings/admin/violation_admin.py (终极修正版 - 2026-01-09 - 更严格的空间管理员权限控制)
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.db.models import Q, Manager, QuerySet

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

from bookings.models import Violation

import logging

logger = logging.getLogger(__name__)

# --- 健壮的 Mock 对象定义 (解决 TypeError 和 Unresolved reference) ---
SPACES_MODELS_LOADED = False
try:
    from spaces.models import Space, SpaceType, BookableAmenity

    SPACES_MODELS_LOADED = True
except ImportError:
    class MockValuesListQuerySet(list):
        def __init__(self, *args, **kwargs):
            self._mock_ids = kwargs.pop('_mock_ids', [])
            super().__init__(self._mock_ids)

        def distinct(self):
            return MockValuesListQuerySet(_mock_ids=list(set(self._mock_ids)))

        def values_list(self, *args, **kwargs):
            return self

        def exists(self):
            return bool(self._mock_ids)

        def filter(self, *args, **kwargs):
            return MockValuesListQuerySet(_mock_ids=[])

        def count(self):
            return len(self._mock_ids)


    class MockQuerySet(models.QuerySet):
        def __init__(self, *args, **kwargs):
            self._mock_instances = kwargs.pop('_mock_instances', [])
            super().__init__(*args, **kwargs)

        def none(self):
            return MockQuerySet(self.model, using=self._db, _mock_instances=[])

        def filter(self, *args, **kwargs):
            filtered_instances = []
            for inst in self._mock_instances:
                match = True
                for key, value in kwargs.items():
                    current_obj = inst
                    parts = key.split('__')

                    for i, part in enumerate(parts):
                        if i == len(parts) - 1 and part.endswith('_in'):
                            field_name = part[:-3]
                            attr = getattr(current_obj, field_name, None)
                            if attr is None or (value is not None and attr not in value if isinstance(value,
                                                                                                      (list, tuple,
                                                                                                       set)) else attr != value):
                                match = False
                                break
                        elif i == len(parts) - 1 and part.endswith('_id'):
                            field_name = part[:-3]
                            if not hasattr(current_obj, field_name) or getattr(getattr(current_obj, field_name, None),
                                                                               'id', None) != value:
                                match = False
                                break
                        else:
                            if isinstance(current_obj, (models.Model, object)) and hasattr(current_obj, part):
                                current_obj = getattr(current_obj, part, None)
                                if current_obj is None:
                                    match = False
                                    break
                            else:
                                if getattr(inst, key, None) != value:
                                    match = False
                                break
                    if not match:
                        break
                if match:
                    filtered_instances.append(inst)
            return MockQuerySet(self.model, using=self._db, _mock_instances=filtered_instances)

        def values_list(self, *args, **kwargs):
            extracted_values = []
            flat = kwargs.get('flat', False)
            for inst in self._mock_instances:
                row_values = []
                for field_path in args:
                    current_val = inst
                    for part in field_path.split('__'):
                        current_val = getattr(current_val, part, None)
                        if current_val is None:
                            break
                    row_values.append(current_val)
                if all(v is not None for v in row_values):
                    extracted_values.append(row_values[0] if flat and len(row_values) == 1 else tuple(row_values))

            return MockValuesListQuerySet(_mock_ids=[v for v in extracted_values if v is not None])

        def distinct(self):
            seen_ids = set()
            distinct_instances = []
            for inst in self._mock_instances:
                instance_id = getattr(inst, 'id', hash(inst))
                if instance_id not in seen_ids:
                    distinct_instances.append(inst)
                    seen_ids.add(instance_id)
            return MockQuerySet(self.model, using=self._db, _mock_instances=distinct_instances)

        def exists(self):
            return bool(self._mock_instances)

        def all(self):
            return self._mock_instances

        def first(self):
            return self._mock_instances[0] if self._mock_instances else None

        def count(self):
            return len(self._mock_instances)

        def update(self, **kwargs):
            return len(self._mock_instances)

        def select_related(self, *args, **kwargs):
            return self


    class MockManager(models.Manager):
        def get_queryset(self):
            return MockQuerySet(self.model, using=self._db, _mock_instances=[])


    class MockSpace(models.Model):
        name = "Mock Space"
        requires_approval = False
        space_type = None
        objects = MockManager()
        id = None
        _state = None

        def __str__(self): return self.name

        def __init__(self, id=1, name="Mock Space", requires_approval=False, space_type=None, _state=None):
            self.id = id
            self.name = name
            self.requires_approval = requires_approval
            self.space_type = space_type if space_type is not None else MockSpaceType(id=99)
            self._state = _state if _state is not None else models.base.ModelState()

        def has_perm(self, perm, obj=None): return True


    class MockSpaceType(models.Model):
        name = "Mock SpaceType"
        objects = MockManager()
        id = None
        _state = None

        def __str__(self): return self.name

        def __init__(self, id=99, name="Mock SpaceType", _state=None):
            self.id = id
            self.name = name
            self._state = _state if _state is not None else models.base.ModelState()


    class MockBookableAmenity(models.Model):
        amenity = None
        space = None
        objects = MockManager()
        id = None
        _state = None

        def __str__(self): return "Mock BookableAmenity"

        def __init__(self, id=1, amenity=None, space=None, _state=None):
            self.id = id
            self.amenity = amenity if amenity is not None else MockSpaceType(id=98)
            self.space = space if space is not None else MockSpace(id=97)
            self._state = _state if _state is not None else models.base.ModelState()


    Space = MockSpace
    SpaceType = MockSpaceType
    BookableAmenity = MockBookableAmenity
    logger.warning(
        "Warning: Missing modules from 'spaces' app. Using robust mock objects to maintain functionality in bookings/admin/violation_admin.py. Functionality may be limited.")


# --- Mock 定义结束 ---

@admin.register(Violation)
class ViolationAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'user_display', 'booking_id_display', 'violation_type', 'space_type_display', 'penalty_points',
        'issued_at', 'is_resolved', 'resolved_at_display', 'issued_by_display', 'resolved_by_display'
    )
    list_filter = ('violation_type', 'is_resolved', 'issued_at', 'user', 'issued_by', 'space_type')
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name',
        'booking__space__name', 'booking__bookable_amenity__amenity__name',
        'description', 'space_type__name'
    )
    date_hierarchy = 'issued_at'
    raw_id_fields = ('user', 'booking', 'issued_by', 'resolved_by', 'space_type')
    fieldsets = (
        (None, {'fields': (('user', 'booking'), 'violation_type', 'space_type', 'description',
                           ('penalty_points', 'is_resolved'))}),
        ('记录与解决信息', {'fields': (('issued_by', 'issued_at'), ('resolved_by', 'resolved_at'))}),
    )
    readonly_fields = ('issued_at',)  # 空间管理员可以设置 issued_by 和 resolved_by，但 issued_at 是自动的

    def save_model(self, request, obj: 'Violation', form, change):
        if not request.user.is_authenticated:
            messages.error(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            raise ValidationError('没有权限')

        # Superuser/SystemAdmin 拥有所有权限，不受此限制。
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            pass  # No additional checks needed
        else:
            # 空间管理员可以创建和解决记录，但不能编辑其他字段
            # 只有当用户有明确权限进行创建/解决行为时才允许
            if not (request.user.has_perm('bookings.can_create_violation_record') or
                    request.user.has_perm('bookings.can_resolve_violation_record')):
                # 由于创建和解决权限是模型级的（在 save_model 中难以区分 obj 之前状态），
                # 我们需要更细粒度的检查。
                # 对于 SpaceManager，他们应该通过 Admin Action 来创建 Violation
                # 而解决 Violation 则是通过 is_resolved 字段。
                # 如果是 SpaceManager，并且尝试修改非解决状态的字段，则不允许
                if not obj.is_resolved:  # 如果不是解决操作，且不是创建，则不允许
                    messages.error(request, f"您没有权限修改此违规记录(ID:{obj.pk})的非解决状态字段。", messages.ERROR)
                    raise ValidationError('没有权限')

            # 如果是 SpaceManager 并且是修改操作 (不是创建)，则只允许修改与解决相关的字段
            # (is_resolved, resolved_by, resolved_at)
            # 在 has_change_permission 中限制了对非解决字段的 direct change
            if getattr(request.user, 'is_space_manager', False) and change:
                # 检查哪些字段被修改了
                changed_fields = form.changed_data
                allowed_fields_for_spaceman_edit = {'is_resolved', 'resolved_by', 'resolved_at'}

                # 如果修改了非允许的字段，则拒绝
                if not changed_fields.issubset(allowed_fields_for_spaceman_edit):
                    messages.error(request, f"您没有权限修改此违规记录(ID: {obj.pk})的非解决状态字段。")
                    raise ValidationError('没有权限')

        # 确保 space_type 被填充 (如果是由 Booking 触发，可以自动填充)
        if not obj.space_type and obj.booking:
            if obj.booking.space and obj.booking.space.space_type:
                obj.space_type = obj.booking.space.space_type
            elif obj.booking.bookable_amenity and obj.booking.bookable_amenity.space \
                    and obj.booking.bookable_amenity.space.space_type:
                obj.space_type = obj.bookable_amenity.space.space_type

        # 处理解决状态和记录人员
        if obj.is_resolved and not obj.resolved_at:
            obj.resolved_at = timezone.now()
            obj.resolved_by = request.user
        elif not obj.is_resolved and obj.resolved_at:  # 如果取消解决了
            obj.resolved_at = None
            obj.resolved_by = None

        super().save_model(request, obj, form, change)

    @admin.display(description='预订ID')
    def booking_id_display(self, obj: 'Violation'):
        return obj.booking.id if obj.booking else 'N/A'

    @admin.display(description='用户')
    def user_display(self, obj: 'Violation'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='记录人员')
    def issued_by_display(self, obj: 'Violation'):
        return obj.issued_by.get_full_name if obj.issued_by else 'N/A'

    @admin.display(description='解决人员')
    def resolved_by_display(self, obj: 'Violation'):
        return obj.resolved_by.get_full_name if obj.resolved_by else 'N/A'

    @admin.display(description='解决时间')
    def resolved_at_display(self, obj: 'Violation'):
        return obj.resolved_at if obj.resolved_at else '未解决'

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'Violation'):
        return obj.space_type.name if obj.space_type else 'N/A'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            return qs.select_related('user', 'booking__space', 'booking__bookable_amenity__space',
                                     'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type')
        if not SPACES_MODELS_LOADED:
            messages.warning(request, "Space models not available. Violations cannot be filtered by space permissions.")
            return qs.none()

        # SpaceManager 的 get_queryset: 仅查看其管理空间相关的违规记录
        managed_spaces_ids = get_objects_for_user(
            request.user, 'spaces.can_view_space_bookings', klass=Space
        ).values_list('id', flat=True)

        return qs.filter(
            Q(booking__space__id__in=managed_spaces_ids) |
            Q(booking__bookable_amenity__space__id__in=managed_spaces_ids)
        ).distinct().select_related('user', 'booking__space', 'booking__bookable_amenity__space',
                                    'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type')

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
        if obj is None: return self.has_module_permission(request)

        if not SPACES_MODELS_LOADED: return False

        target_space = obj.booking.space if obj.booking and obj.booking.space else \
            (obj.booking.bookable_amenity.space if obj.booking and obj.booking.bookable_amenity else None)

        if target_space:
            # 针对特定违规记录，检查用户是否对该违规记录关联的 Space 拥有 can_view_space_bookings 对象级权限
            return request.user.has_perm('spaces.can_view_space_bookings', target_space)
        else:  # 如果没有关联空间，且不是系统管理员，则不能看
            return False  # 以前是 return True for superuser/sysadmin, now explicitly False for spaceman

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        # 空间管理员不能直接通过 Admin 后台添加违规记录，应通过 Admin Action
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        if obj is None: return self.has_module_permission(request)  # 对于列表页，依赖 module 权限

        if not SPACES_MODELS_LOADED: return False

        # 空间管理员只能修改其负责空间的违规记录的“解决状态”
        # 所以对于 SpaceManager，除了系统管理员，has_change_permission 应该返回 False
        # 除非要修改的 obj 是为了解决状态 (但这会被 save_model 的逻辑拦截，所以这里直接返回 False 更安全)
        if getattr(request.user, 'is_space_manager', False):
            # 只有在obj.is_resolved状态需要被修改时才放行，但这个逻辑最好在 save_model 里进一步细化
            # 为了防止编辑其他字段，这里直接返回 False 即可。
            return False

        # 对于系统管理员，依然检查对象级权限，通常是 can_view_space_bookings
        target_space = obj.booking.space if obj.booking and obj.booking.space else \
            (obj.booking.bookable_amenity.space if obj.booking and obj.booking.bookable_amenity else None)

        # 系统管理员拥有全局 view_all_violations, 可以编辑
        # 这里实际上是控制非解决状态字段的直接编辑
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        # 空间管理员不能删除违规记录
        return request.user.is_superuser  # 只有超级用户可以删除

    def get_actions(self, request):
        # 空间管理员可以执行 'mark_resolved' 动作 (通过模型级权限 bookings.can_resolve_violation_record 开启)
        if not request.user.is_authenticated:
            return {}

        actions = super().get_actions(request)

        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            if 'delete_selected' in actions:
                del actions['delete_selected']
        else:  # 对于 SpaceManager
            allowed_actions_for_space_manager = ['mark_resolved']  # 目前仅允许解决违规

            current_action_names = list(actions.keys())
            for action_name in current_action_names:
                if action_name not in allowed_actions_for_space_manager:
                    actions.pop(action_name, None)
        return actions

    @admin.action(description="解决选择的违规记录")
    def mark_resolved(self, request, queryset):
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        resolved_count = 0
        for violation in queryset:
            target_space = None
            if violation.booking:
                target_space = violation.booking.space or (
                    violation.booking.bookable_amenity.space if violation.booking.bookable_amenity else None)

            # 权限检查：系统管理员或拥有 bookings.can_resolve_violation_record 模型级权限的 SpaceManager
            # 且 SpaceManager 必须对此违规记录关联的空间有查看预订的权限
            if request.user.is_superuser or getattr(request.user, 'is_system_admin', False) or \
                    (getattr(request.user, 'is_space_manager', False) and
                     request.user.has_perm('bookings.can_resolve_violation_record') and
                     (not target_space or request.user.has_perm('spaces.can_view_space_bookings', target_space))):

                if not violation.is_resolved:
                    violation.is_resolved = True
                    violation.resolved_by = request.user
                    violation.resolved_at = timezone.now()
                    violation.save(update_fields=['is_resolved', 'resolved_by', 'resolved_at'])
                    resolved_count += 1
                else:
                    self.message_user(request, f"违规记录 {violation.id} 已解决，无需重复操作。", messages.WARNING)
            else:
                self.message_user(request, f"您没有权限解决违规记录 {violation.id}。", messages.ERROR)

        self.message_user(request, f"成功解决了 {resolved_count} 条违规记录。", messages.SUCCESS)