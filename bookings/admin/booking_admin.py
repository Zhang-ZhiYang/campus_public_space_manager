# bookings/admin/booking_admin.py (终极修正版 - 2026-01-09 - 更严格的空间管理员权限控制)
from django.contrib import admin
from django.db import transaction, models
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, Manager, QuerySet
from django.core.exceptions import ValidationError

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

from django.conf import settings

CustomUser = settings.AUTH_USER_MODEL

from bookings.models import Booking
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
        "Warning: Missing modules from 'spaces' app. Using robust mock objects to maintain functionality in bookings/admin/booking_admin.py. Functionality may be limited.")


# --- Mock 定义结束 ---

@admin.register(Booking)
class BookingAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'user_display', 'booking_target_display', 'status', 'booked_quantity',
        'start_time', 'end_time', 'reviewed_by_display', 'requires_approval_status'
    )
    list_filter = (
        'status', 'start_time', 'end_time', 'space__space_type', 'space',
        'bookable_amenity__amenity', 'user', 'reviewed_by'
    )
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name', 'purpose',
        'space__name', 'bookable_amenity__space__name', 'bookable_amenity__amenity__name'
    )
    raw_id_fields = ('user', 'space', 'bookable_amenity', 'reviewed_by')
    date_hierarchy = 'start_time'

    fieldsets = (
        (None, {'fields': (('user', 'status'), ('space', 'bookable_amenity', 'booked_quantity'), 'purpose',)}),
        ('时间信息', {'fields': (('start_time', 'end_time'),)}),
        ('审核信息', {'fields': ('admin_notes', ('reviewed_by', 'reviewed_at'))}),
        ('系统信息', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)})
    )
    readonly_fields = ('created_at', 'updated_at', 'reviewed_at')

    actions = ['approve_bookings', 'reject_bookings', 'cancel_bookings', 'mark_completed_bookings', 'mark_checked_in',
               'mark_no_show_and_violate']

    @admin.display(description='预订用户')
    def user_display(self, obj: 'Booking'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='审核者')
    def reviewed_by_display(self, obj: 'Booking'):
        return obj.reviewed_by.get_full_name if obj.reviewed_by else 'N/A'

    @admin.display(description='预订目标')
    def booking_target_display(self, obj: 'Booking'):
        if obj.bookable_amenity:
            amenity_val = getattr(obj.bookable_amenity, 'amenity', None)
            space_val = getattr(obj.bookable_amenity, 'space', None)
            amenity_name = amenity_val.name if amenity_val else "未知设施类型"
            space_name = space_val.name if space_val else "未知空间"
            return f"设施: {amenity_name} in {space_name}"
        elif obj.space:
            return f"空间: {obj.space.name}"
        return "N/A"

    @admin.display(description='是否需审批')
    def requires_approval_status(self, obj: 'Booking'):
        target_obj = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        return bool(target_obj and getattr(target_obj, 'requires_approval', False))

    requires_approval_status.boolean = True

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            return qs.select_related('user', 'reviewed_by', 'space', 'bookable_amenity__amenity',
                                     'bookable_amenity__space')
        if not SPACES_MODELS_LOADED:
            messages.warning(request, "Space models not available. Bookings cannot be filtered by space permissions.")
            return qs.none()

        # SpaceManager 的 get_queryset 依赖于对象级权限
        managed_spaces_ids = get_objects_for_user(
            request.user, 'spaces.can_view_space_bookings', klass=Space
        ).values_list('id', flat=True)

        managed_amenities_ids = get_objects_for_user(
            request.user, 'spaces.can_view_bookable_amenity', klass=BookableAmenity
        ).values_list('id', flat=True)

        return qs.filter(
            Q(space__id__in=managed_spaces_ids) | Q(bookable_amenity__id__in=managed_amenities_ids)
        ).select_related('user', 'reviewed_by', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space')

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

        target_space = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        if not (target_space and SPACES_MODELS_LOADED): return False
        # 针对特定预订对象，检查用户是否对该预订所在的 Scepace 拥有 can_view_space_bookings 对象级权限
        return request.user.has_perm('spaces.can_view_space_bookings', target_space)

    def has_add_permission(self, request):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            return True
        # 空间管理员不能直接通过 Admin 后台添加预订
        return False

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True

        # 空间管理员不能直接通过 Admin 后台修改预订，所有修改都通过 Admin Actions
        return False

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        # 空间管理员不能直接通过 Admin 后台删除预订
        return False

    def get_actions(self, request):
        if not request.user.is_authenticated: return {}
        actions = super().get_actions(request)
        if not (request.user.is_superuser or getattr(request.user, 'is_system_admin', False)):
            space_manager_specific_actions = [
                'approve_bookings', 'reject_bookings', 'cancel_bookings', 'mark_completed_bookings',
                'mark_checked_in', 'mark_no_show_and_violate'
            ]
            actions.pop('delete_selected', None)

            filtered_actions = {}
            for action_name in space_manager_specific_actions:
                if action_name in actions:
                    filtered_actions[action_name] = actions[action_name]
            return filtered_actions
        else:
            if 'delete_selected' in actions: del actions['delete_selected']
            return actions

    @admin.action(description="批准选择的预订")
    def approve_bookings(self, request, queryset):
        if not request.user.is_authenticated: self.message_user(request, "您没有权限执行此操作，请先登录。",
                                                                messages.ERROR); return
        approved_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if not target_space:
                self.message_user(request, f"预订 {booking.id} 的目标空间无效，无法批准。", messages.ERROR)
                continue

            if request.user.is_superuser or request.user.is_system_admin or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_approve_space_bookings', target_space)):
                if booking.status == 'PENDING':
                    booking.status = 'APPROVED'
                    booking.reviewed_by = request.user
                    booking.reviewed_at = timezone.now()
                    booking.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
                    approved_count += 1
                else:
                    self.message_user(request, f"预订 {booking.id} 状态为 {booking.status}，无法批准。", messages.WARNING)
            else:
                self.message_user(request, f"您没有权限批准预订 {booking.id}。", messages.ERROR)
        self.message_user(request, f"成功批准了 {approved_count} 条预订。", messages.SUCCESS)

    @admin.action(description="拒绝选择的预订")
    def reject_bookings(self, request, queryset):
        if not request.user.is_authenticated: self.message_user(request, "您没有权限执行此操作，请先登录。",
                                                                messages.ERROR); return
        rejected_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if not target_space:
                self.message_user(request, f"预订 {booking.id} 的目标空间无效，无法拒绝。", messages.ERROR)
                continue

            if request.user.is_superuser or request.user.is_system_admin or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_approve_space_bookings', target_space)):
                if booking.status == 'PENDING':
                    booking.status = 'REJECTED'
                    booking.reviewed_by = request.user
                    booking.reviewed_at = timezone.now()
                    booking.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
                    rejected_count += 1
                else:
                    self.message_user(request, f"预订 {booking.id} 状态为 {booking.status}，无法拒绝。", messages.WARNING)
            else:
                self.message_user(request, f"您没有权限拒绝预订 {booking.id}。", messages.ERROR)
        self.message_user(request, f"成功拒绝了 {rejected_count} 条预订。", messages.SUCCESS)

    @admin.action(description="取消选择的预订")
    def cancel_bookings(self, request, queryset):
        if not request.user.is_authenticated: self.message_user(request, "您没有权限执行此操作，请先登录。",
                                                                messages.ERROR); return
        cancelled_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if not target_space:
                self.message_user(request, f"预订 {booking.id} 的目标空间无效，无法取消。", messages.ERROR)
                continue

            if request.user.is_superuser or request.user.is_system_admin or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_cancel_space_bookings', target_space)):
                if booking.status in ['PENDING', 'APPROVED', 'CHECKED_IN']:
                    booking.status = 'CANCELLED'
                    booking.reviewed_by = request.user
                    booking.reviewed_at = timezone.now()
                    booking.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
                    cancelled_count += 1
                else:
                    self.message_user(request, f"预订 {booking.id} 状态为 {booking.status}，无法取消。", messages.WARNING)
            else:
                self.message_user(request, f"您没有权限取消预订 {booking.id}。", messages.ERROR)
        self.message_user(request, f"成功取消了 {cancelled_count} 条预订。", messages.SUCCESS)

    @admin.action(description="标记为已完成")
    def mark_completed_bookings(self, request, queryset):
        if not request.user.is_authenticated: self.message_user(request, "您没有权限执行此操作，请先登录。",
                                                                messages.ERROR); return
        completed_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if not target_space:
                self.message_user(request, f"预订 {booking.id} 的目标空间无效，无法标记为已完成。", messages.ERROR)
                continue

            if request.user.is_superuser or request.user.is_system_admin or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_checkin_space_bookings', target_space)):
                if booking.status == 'CHECKED_IN':
                    booking.status = 'COMPLETED'
                    booking.save(update_fields=['status'])
                    completed_count += 1
                else:
                    self.message_user(request, f"预订 {booking.id} 状态为 {booking.status}，无法标记为已完成。",
                                      messages.WARNING)
            else:
                self.message_user(request, f"您没有权限标记预订 {booking.id} 为已完成。", messages.ERROR)
        self.message_user(request, f"成功标记 {completed_count} 条预订为已完成。", messages.SUCCESS)

    @admin.action(description="标记为已签到")
    def mark_checked_in(self, request, queryset):
        if not request.user.is_authenticated: self.message_user(request, "您没有权限执行此操作，请先登录。",
                                                                messages.ERROR); return
        checked_in_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if not target_space:
                self.message_user(request, f"预订 {booking.id} 的目标空间无效，无法签到。", messages.ERROR)
                continue

            if request.user.is_superuser or request.user.is_system_admin or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_checkin_space_bookings', target_space)):
                if booking.status in ['APPROVED', 'PENDING']:
                    booking.status = 'CHECKED_IN'
                    booking.save(update_fields=['status'])
                    checked_in_count += 1
                else:
                    self.message_user(request, f"预订 {booking.id} 状态为 {booking.status}，无法签到。", messages.WARNING)
            else:
                self.message_user(request, f"您没有权限标记预订 {booking.id} 为已签到。", messages.ERROR)
        self.message_user(request, f"成功标记 {checked_in_count} 条预订为已签到。", messages.SUCCESS)

    @admin.action(description="标记为未到场并创建违规记录")
    def mark_no_show_and_violate(self, request, queryset):
        if not request.user.is_authenticated: self.message_user(request, "您没有权限执行此操作，请先登录。",
                                                                messages.ERROR); return
        no_show_count = 0
        violation_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if not target_space:
                self.message_user(request, f"预订 {booking.id} 的目标空间无效，无法标记为未到场或创建违规记录。",
                                  messages.ERROR)
                continue

            if request.user.is_superuser or request.user.is_system_admin or \
                    (getattr(request.user, 'is_space_manager', False) and request.user.has_perm(
                        'spaces.can_checkin_space_bookings', target_space)):
                if booking.status in ['PENDING', 'APPROVED'] and booking.end_time < timezone.now():
                    booking.status = 'NO_SHOW'
                    booking.save(update_fields=['status'])
                    no_show_count += 1
                    space_type_for_violation = target_space.space_type if target_space else None
                    if space_type_for_violation:
                        Violation.objects.create(
                            user=booking.user, booking=booking, space_type=space_type_for_violation,
                            violation_type='NO_SHOW',
                            description=f"用户 {booking.user.get_full_name} 未在 {getattr(target_space, 'name', '未知空间')} 预订中签到。",
                            issued_by=request.user, penalty_points=1
                        )
                        violation_count += 1
                    else:
                        self.message_user(request, f"预订 {booking.id} 无法确定空间类型，未能创建违规记录。",
                                          messages.WARNING)
                else:
                    self.message_user(request, f"预订 {booking.id} 状态为 {booking.status} 或未过期，无法标记为未到场。",
                                      messages.WARNING)
            else:
                self.message_user(request, f"您没有权限对预订 {booking.id} 进行未到场标记或创建违规记录。",
                                  messages.ERROR)
        self.message_user(request, f"成功标记 {no_show_count} 条预订为未到场，创建 {violation_count} 条违规记录。",
                          messages.SUCCESS)