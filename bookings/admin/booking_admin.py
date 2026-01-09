# bookings/admin/booking_admin.py (终极修正版 - 2026-01-09 - 禁用 SpaceManager 的直接增删改权限)
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
    # 用于 Mock values_list() 返回结果，使其可迭代且支持 distinct()
    class MockValuesListQuerySet(list):
        def __init__(self, *args, **kwargs):
            self._mock_ids = kwargs.pop('_mock_ids', [])
            super().__init__(self._mock_ids)  # list初始化时就包含这些元素

        def distinct(self):
            return MockValuesListQuerySet(_mock_ids=list(set(self._mock_ids)))

        def values_list(self, *args, **kwargs):
            # 如果在MockValuesListQuerySet上再次调用values_list，就返回自身
            return self

        def exists(self):
            return bool(self._mock_ids)

        def filter(self, *args, **kwargs):
            return MockValuesListQuerySet(_mock_ids=[])  # 简化过滤，返回空集

        def count(self):
            return len(self._mock_ids)


    # 主 Mock QuerySet，模拟Django QuerySet的大部分行为
    class MockQuerySet(models.QuerySet):
        def __init__(self, *args, **kwargs):
            self._mock_instances = kwargs.pop('_mock_instances', [])
            super().__init__(*args, **kwargs)

        def none(self):
            return MockQuerySet(self.model, using=self._db, _mock_instances=[])

        def filter(self, *args, **kwargs):
            # 简化但更健壮的Mock filter逻辑，支持多级关联和__in
            filtered_instances = []
            for inst in self._mock_instances:
                match = True
                for key, value in kwargs.items():
                    current_obj = inst
                    parts = key.split('__')

                    for i, part in enumerate(parts):
                        if i == len(parts) - 1 and part.endswith('_in'):  # 处理 __in 后缀
                            field_name = part[:-3]  # 移除 '_in'
                            attr = getattr(current_obj, field_name, None)
                            if attr is None or (value is not None and attr not in value if isinstance(value,
                                                                                                      (list, tuple,
                                                                                                       set)) else attr != value):
                                match = False
                                break
                        elif i == len(parts) - 1 and part.endswith('_id'):  # 处理 __id 后缀
                            field_name = part[:-3]
                            if not hasattr(current_obj, field_name) or getattr(getattr(current_obj, field_name, None),
                                                                               'id', None) != value:
                                match = False
                                break
                        else:  # 直接属性或关联对象
                            if isinstance(current_obj, (models.Model, object)) and hasattr(current_obj, part):
                                current_obj = getattr(current_obj, part, None)
                                if current_obj is None:  # 关联对象不存在
                                    match = False
                                    break
                            else:  # 简单属性，直接比较
                                if getattr(inst, key, None) != value:
                                    match = False
                                break  # Attribute not found or mismatch
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
                    # 遍历字段路径
                    for part in field_path.split('__'):
                        current_val = getattr(current_val, part, None)
                        if current_val is None:
                            break
                    row_values.append(current_val)
                # 仅当所有提取值都不为None时才添加
                if all(v is not None for v in row_values):
                    extracted_values.append(row_values[0] if flat and len(row_values) == 1 else tuple(row_values))

            # 返回自定义的MockValuesListQuerySet，支持链式调用
            return MockValuesListQuerySet(_mock_ids=[v for v in extracted_values if v is not None])

        def distinct(self):
            # 基于ID进行去重，如果没有ID就用对象哈希
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
            return self._mock_instances  # 默认返回所有模拟实例

        def first(self):
            return self._mock_instances[0] if self._mock_instances else None

        def count(self):
            return len(self._mock_instances)

        def update(self, **kwargs):
            return len(self._mock_instances)  # 简化update

        def select_related(self, *args, **kwargs):
            return self  # Mock select_related


    class MockManager(models.Manager):
        def get_queryset(self):
            return MockQuerySet(self.model, using=self._db, _mock_instances=[])


    # Mock 相关的 models (添加 _state 属性，避免Django内部方法报错)
    class MockSpace(models.Model):
        name = "Mock Space"
        requires_approval = False
        space_type = None
        objects = MockManager()
        id = None
        _state = None  # 添加 _state 属性

        def __str__(self): return self.name

        def __init__(self, id=1, name="Mock Space", requires_approval=False, space_type=None, _state=None):
            self.id = id
            self.name = name
            self.requires_approval = requires_approval
            self.space_type = space_type if space_type is not None else MockSpaceType(id=99)
            self._state = _state if _state is not None else models.base.ModelState()  # 使用Django的ModelState

        def has_perm(self, perm, obj=None): return True


    class MockSpaceType(models.Model):
        name = "Mock SpaceType"
        objects = MockManager()
        id = None
        _state = None  # 添加 _state 属性

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
        _state = None  # 添加 _state 属性

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

        # SpaceManager 的 get_queryset 依赖于对象级权限，需要模型级权限作前提
        # 这里使用 'spaces.can_view_space_bookings' 作为 SpaceManager 的查看权限依据
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
        # 空间管理员至少需要拥有任何一个空间的相关预订查看权限，才能看到 Booking 模块
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        if getattr(request.user, 'is_space_manager', False) and SPACES_MODELS_LOADED:
            return get_objects_for_user(request.user, 'spaces.can_view_space_bookings', klass=Space).exists() or \
                get_objects_for_user(request.user, 'spaces.can_view_bookable_amenity', klass=BookableAmenity).exists()
        return False

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True

        # 对于 SpaceManager，obj=None (列表页) 的查看权限由 has_module_permission 控制
        # 对于 obj (详情页)，需要针对该对象的空间拥有查看权限
        if obj is None: return self.has_module_permission(request)

        target_space = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        if not (target_space and SPACES_MODELS_LOADED): return False
        return request.user.has_perm('spaces.can_view_space_bookings', target_space)

    def has_add_permission(self, request):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            return True
        # <<--- 修改在这里：SpaceManager 不应能直接通过管理后台创建预订 ---
        return False

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True

        # <<--- 修改在这里：SpaceManager 不应能直接通过管理后台修改预订 ---
        # SpaceManagers 的修改权限应仅限于通过 Admin Actions (批准, 拒绝, 签到, 取消等)
        return False

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False): return True
        # SpaceManager 不拥有全局删除权限，并且 'delete_selected' 动作会被 get_actions 移除
        return False

    def get_actions(self, request):
        if not request.user.is_authenticated: return {}
        actions = super().get_actions(request)
        if not (request.user.is_superuser or getattr(request.user, 'is_system_admin', False)):
            space_manager_specific_actions = [
                'approve_bookings', 'reject_bookings', 'cancel_bookings', 'mark_completed_bookings',
                'mark_checked_in', 'mark_no_show_and_violate'
            ]
            actions.pop('delete_selected', None)  # 空间管理员不能批量删除

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

            # 权限检查：是否对该空间拥有 'can_approve_space_bookings' 对象级权限
            if request.user.is_superuser or request.user.is_system_admin or \
                    (request.user.is_space_manager and request.user.has_perm('spaces.can_approve_space_bookings',
                                                                             target_space)):
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

            # 权限检查：是否对该空间拥有 'can_approve_space_bookings' 对象级权限 (拒绝通常与批准使用相同权限)
            if request.user.is_superuser or request.user.is_system_admin or \
                    (request.user.is_space_manager and request.user.has_perm('spaces.can_approve_space_bookings',
                                                                             target_space)):
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

            # 权限检查：是否对该空间拥有 'can_cancel_space_bookings' 对象级权限
            if request.user.is_superuser or request.user.is_system_admin or \
                    (request.user.is_space_manager and request.user.has_perm('spaces.can_cancel_space_bookings',
                                                                             target_space)):
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

            # 权限检查：是否对该空间拥有 'can_checkin_space_bookings' 对象级权限 (完成通常与签到使用相同权限)
            if request.user.is_superuser or request.user.is_system_admin or \
                    (request.user.is_space_manager and request.user.has_perm('spaces.can_checkin_space_bookings',
                                                                             target_space)):
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

            # 权限检查：是否对该空间拥有 'can_checkin_space_bookings' 对象级权限
            if request.user.is_superuser or request.user.is_system_admin or \
                    (request.user.is_space_manager and request.user.has_perm('spaces.can_checkin_space_bookings',
                                                                             target_space)):
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

            # 权限检查：是否对该空间拥有 'can_checkin_space_bookings' 对象级权限 (未到场通常与签到使用相同权限)
            if request.user.is_superuser or request.user.is_system_admin or \
                    (request.user.is_space_manager and request.user.has_perm('spaces.can_checkin_space_bookings',
                                                                             target_space)):
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