# bookings/admin/ban_policy_admin.py (终极修正版 - SpaceManager 只读，严格控制模块可见性)
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import models

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

from bookings.models import SpaceTypeBanPolicy

import logging

logger = logging.getLogger(__name__)

# --- 健壮的 Mock 对象定义 (解决 TypeError) ---
SPACES_MODELS_LOADED = False
try:
    from spaces.models import Space, SpaceType, BookableAmenity

    SPACES_MODELS_LOADED = True
except ImportError:
    class MockValuesListQuerySet(models.QuerySet):
        def __init__(self, *args, **kwargs):
            self._result_list = kwargs.pop('_mock_ids', [])
            super().__init__(*args, **kwargs)

        def __iter__(self):
            return iter(self._result_list)

        def distinct(self):
            return MockValuesListQuerySet(self.model, using=self._db, _mock_ids=list(set(self._result_list)))

        def values_list(self, *args, **kwargs):
            if kwargs.get('flat', False):
                return self._result_list
            return MockValuesListQuerySet(self.model, using=self._db, _mock_ids=[(x,) for x in self._result_list])

        def exists(self):
            return bool(self._result_list)

        def filter(self, *args, **kwargs):
            return MockValuesListQuerySet(self.model, using=self._db, _mock_ids=[])

        def count(self):
            return len(self._result_list)


    class MockQuerySet(models.QuerySet):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._mock_instances = kwargs.pop('_mock_instances', [])

        def none(self):
            return MockQuerySet(self.model, using=self._db)

        def filter(self, *args, **kwargs):
            filtered_instances = []
            for inst in self._mock_instances:
                match = True
                for key, value in kwargs.items():
                    if key.endswith('__in'):
                        field = key.replace('__in', '')
                        if '__' in field:
                            parts = field.split('__')
                            current_val = inst
                            for part in parts:
                                current_val = getattr(current_val, part, None)
                                if current_val is None:
                                    break
                            if current_val not in value:
                                match = False
                                break
                        else:
                            if getattr(inst, field, None) not in value:
                                match = False
                                break
                    elif key.endswith('__id'):
                        field_name = key.replace('__id', '')
                        related_obj = getattr(inst, field_name, None)
                        if related_obj and getattr(related_obj, 'id', None) != value:
                            match = False
                            break
                    else:
                        if isinstance(inst, models.Model) and hasattr(inst, key) and getattr(inst, key, None) != value:
                            match = False
                            break
                if match:
                    filtered_instances.append(inst)
            return MockQuerySet(self.model, using=self._db, _mock_instances=filtered_instances)

        def values_list(self, *args, **kwargs):
            extracted_values = []
            for inst in self._mock_instances:
                row_values = []
                for field_path in args:
                    current_val = inst
                    for part in field_path.split('__'):
                        current_val = getattr(current_val, part, None)
                        if current_val is None: break
                    row_values.append(current_val)
                if row_values:
                    extracted_values.append(
                        row_values[0] if kwargs.get('flat', False) and len(row_values) == 1 else tuple(row_values))

            return MockValuesListQuerySet(self.model, using=self._db,
                                          _mock_ids=[v for v in extracted_values if v is not None])

        def distinct(self):
            seen_ids = set()
            distinct_instances = []
            for inst in self._mock_instances:
                inst_id = getattr(inst, 'id', hash(inst))
                if inst_id not in seen_ids:
                    distinct_instances.append(inst)
                    seen_ids.add(inst_id)
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

        def __str__(self): return self.name

        def __init__(self, id=1, name="Mock Space", requires_approval=False, space_type=None, _state=None):
            self.id = id
            self.name = name
            self.requires_approval = requires_approval
            self.space_type = space_type if space_type is not None else MockSpaceType(id=99)
            self._state = _state if _state is not None else object()

        def has_perm(self, perm, obj=None):
            return True


    class MockSpaceType(models.Model):
        name = "Mock SpaceType"
        objects = MockManager()
        id = None

        def __str__(self): return self.name

        def __init__(self, id=99, name="Mock SpaceType", _state=None):
            self.id = id
            self.name = name
            self._state = _state if _state is not None else object()


    class MockBookableAmenity(models.Model):
        amenity = None
        space = None
        objects = MockManager()
        id = None

        def __str__(self): return "Mock BookableAmenity"

        def __init__(self, id=1, amenity=None, space=None, _state=None):
            self.id = id
            self.amenity = amenity if amenity is not None else MockSpaceType(id=98)
            self.space = space if space is not None else MockSpace(id=97)
            self._state = _state if _state is not None else object()


    Space = MockSpace
    SpaceType = MockSpaceType
    BookableAmenity = MockBookableAmenity
    logger.warning(
        "Warning: Missing modules from 'spaces' app. Using robust mock objects to maintain functionality in bookings/admin/ban_policy_admin.py. Functionality may be limited.")


# --- Mock 定义结束 ---

@admin.register(SpaceTypeBanPolicy)
class SpaceTypeBanPolicyAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'space_type_display', 'threshold_points', 'ban_duration',
        'priority', 'is_active', 'description'
    )
    list_filter = ('is_active', 'space_type', 'priority')
    search_fields = ('description', 'space_type__name')
    raw_id_fields = ('space_type',)
    fieldsets = (
        (None,
         {'fields': ('space_type', ('threshold_points', 'ban_duration'), 'priority', 'is_active', 'description')}),
        ('系统信息', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)})
    )
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'SpaceTypeBanPolicy'):
        return obj.space_type.name if obj.space_type else '全局'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()

        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            return qs.select_related('space_type')

        if not SPACES_MODELS_LOADED:
            messages.warning(request,
                             "Space models not available. Ban policies cannot be filtered by space permissions.")
            return qs.none()

        # 空间管理员只能查看其管理空间对应的空间类型的禁用策略
        managed_spacetype_ids = []
        if getattr(request.user, 'is_space_manager', False):
            managed_spaces = get_objects_for_user(
                request.user, 'spaces.can_view_space_bookings', klass=Space
            )
            managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
            managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

        return qs.filter(space_type__id__in=managed_spacetype_ids).select_related('space_type')

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

        if obj.space_type:
            # 针对特定对象，检查用户是否管理该空间类型下的某个空间
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_view_space_bookings', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # 全局禁用策略，只有系统管理员可见
            return False

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        # 空间管理员不能添加
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        # 空间管理员不能修改
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        # 空间管理员不能删除
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)