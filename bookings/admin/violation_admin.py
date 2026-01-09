# bookings/admin/violation_admin.py (终极修正版 - 2026-01-09 - 修正 get_actions 错误)
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError  # <-- 确保已导入
from django.db import models
from django.utils import timezone
from django.db.models import Q, Manager, QuerySet

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

from bookings.models import Violation

import logging

logger = logging.getLogger(__name__)

# --- 健壮的 Mock 对象定义 (解决 TypeError 和 Unresolved reference) ---
# (此部分保持不变，因为问题不在Mock对象本身，而在Admin动作处理逻辑)
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
    readonly_fields = ('issued_at',)

    def save_model(self, request, obj: 'Violation', form, change):
        if not request.user.is_authenticated:
            messages.error(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            raise ValidationError('没有权限')

        if not obj.space_type and obj.booking:
            if obj.booking.space and obj.booking.space.space_type:
                obj.space_type = obj.booking.space.space_type
            elif obj.booking.bookable_amenity and obj.booking.bookable_amenity.space \
                    and obj.booking.bookable_amenity.space.space_type:
                obj.space_type = obj.bookable_amenity.space.space_type

        # 权限检查：只有超级用户/系统管理员或管理其关联 Booking 的 Space 的空间管理员才能修改
        if not (request.user.is_superuser or getattr(request.user, 'is_system_admin', False)):
            target_space = None
            if obj.booking:
                if obj.booking.space:
                    target_space = obj.booking.space
                elif obj.booking.bookable_amenity and obj.booking.bookable_amenity.space:
                    target_space = obj.booking.bookable_amenity.space

            if target_space:
                if not SPACES_MODELS_LOADED:
                    messages.error(request, "Space models not available. Cannot check permissions.", messages.ERROR)
                    raise ValidationError('模型不可用，无法检查权限')

                if not request.user.has_perm('spaces.can_view_space_bookings', target_space):
                    messages.error(request, f"您没有权限修改此违规记录(ID: {obj.pk})，因为您不管理其关联的预订空间。",
                                   messages.ERROR)
                    raise ValidationError('没有权限')
            else:
                messages.error(request, f"您没有权限修改此违规记录(ID: {obj.pk})，因为它没有关联到您管理的预订空间。",
                               messages.ERROR)
                raise ValidationError('没有权限')

        if obj.is_resolved and not obj.resolved_at:
            obj.resolved_at = timezone.now()
            obj.resolved_by = request.user
        elif not obj.is_resolved and obj.resolved_at:
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

        managed_spaces_ids = get_objects_for_user(
            request.user, 'spaces.can_view_space_bookings', klass=Space
        ).values_list('id', flat=True)

        return qs.filter(
            Q(booking__space__id__in=managed_spaces_ids) |
            Q(booking__bookable_amenity__space__id__in=managed_spaces_ids)
        ).distinct().select_related('user', 'booking__space', 'booking__bookable_amenity__space',
                                    'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type')

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        if getattr(request.user, 'is_space_manager', False):
            return SPACES_MODELS_LOADED and get_objects_for_user(request.user, 'spaces.can_view_space_bookings',
                                                                 klass=Space).exists()
        return False

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        if obj is None: return self.has_module_permission(request)

        if not SPACES_MODELS_LOADED: return False

        target_space = obj.booking.space if obj.booking and obj.booking.space else \
            (obj.booking.bookable_amenity.space if obj.booking and obj.booking.bookable_amenity else None)

        if target_space:
            return request.user.has_perm('spaces.can_view_space_bookings', target_space)
        else:
            return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        if obj is None: return self.has_module_permission(request)

        if not SPACES_MODELS_LOADED: return False

        target_space = obj.booking.space if obj.booking and obj.booking.space else \
            (obj.booking.bookable_amenity.space if obj.booking and obj.booking.bookable_amenity else None)

        if target_space:
            return request.user.has_perm('spaces.can_view_space_bookings', target_space)
        else:
            return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser

    def get_actions(self, request):
        if not request.user.is_authenticated:
            return {}

        actions = super().get_actions(request)  # Get all default actions, they are already tuples

        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            # Superusers/SystemAdmins get all actions except 'delete_selected'
            if 'delete_selected' in actions:
                del actions['delete_selected']
        else:
            # For space managers, only allow 'mark_resolved'
            # We explicitly define a new dictionary to ensure only allowed actions are present,
            # and they maintain the (func, name, description) tuple format as returned by @admin.action
            allowed_actions = {}
            if 'mark_resolved' in actions:
                allowed_actions['mark_resolved'] = actions['mark_resolved']
            actions = allowed_actions  # Replace the actions with only allowed ones

        return actions  # Return the filtered actions dictionary