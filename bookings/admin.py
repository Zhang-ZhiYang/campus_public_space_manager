# bookings/admin.py
from django.contrib import admin
from django.db import transaction, models
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, Manager, QuerySet

# 导入 GuardedModelAdmin
from guardian.admin import GuardedModelAdmin
# CRITICAL FIX: 导入 get_objects_for_user
from guardian.shortcuts import get_objects_for_user

# 导入 CustomUser 和 Space 相关模型
from django.conf import settings

CustomUser = settings.AUTH_USER_MODEL

# 直接导入本应用的模型
from .models import (
    Booking,
    Violation,
    UserPenaltyPointsPerSpaceType,
    SpaceTypeBanPolicy,
    UserSpaceTypeBan,
    UserSpaceTypeExemption
)

# 确保 SPACES_MODELS_LOADED 标志存在
SPACES_MODELS_LOADED = False
try:
    from spaces.models import Space, SpaceType, BookableAmenity

    SPACES_MODELS_LOADED = True
except ImportError:
    # Define custom mock QuerySets and Managers without for_user
    class MockQuerySet(QuerySet):
        def none(self): return self

        def filter(self, *args, **kwargs): return self

        def values_list(self, *args, **kwargs): return []


    class MockManager(Manager):
        def get_queryset(self):
            return MockQuerySet(self.model, using=self._db)


    class MockSpace(models.Model):
        name = "Mock Space"
        requires_approval = False
        space_type = None
        objects = MockManager()

        def __str__(self): return self.name

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.name = kwargs.pop('name', "Mock Space")
            self.requires_approval = kwargs.pop('requires_approval', False)
            self.space_type = kwargs.pop('space_type', None)


    class MockSpaceType(models.Model):
        name = "Mock SpaceType"
        objects = MockManager()

        def __str__(self): return self.name

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.name = kwargs.pop('name', "Mock SpaceType")
            self.spaces = MockManager()


    class MockBookableAmenity(models.Model):
        amenity = MockSpaceType()
        space = MockSpace()
        objects = MockManager()

        def __str__(self): return "Mock BookableAmenity"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.amenity = kwargs.pop('amenity', MockSpaceType())
            self.space = kwargs.pop('space', MockSpace())


    Space = MockSpace
    SpaceType = MockSpaceType
    BookableAmenity = MockBookableAmenity
    print(
        "Warning: Missing modules from 'spaces' app. Using robust mock objects in bookings/admin.py. Functionality may be limited.")


# ====================================================================
# Booking Admin (预订管理)
# ====================================================================
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
        return "是" if target_obj and getattr(target_obj, 'requires_approval', False) else "否"

    requires_approval_status.boolean = True

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'reviewed_by', 'space', 'bookable_amenity__amenity',
                                     'bookable_amenity__space')
        if not SPACES_MODELS_LOADED:
            messages.warning(request, "Space models not available. Bookings cannot be filtered by space permissions.")
            return qs.none()

        # 获取用户有 'spaces.can_manage_space_bookings' 权限的所有 Space 对象的 ID
        managed_spaces_ids = get_objects_for_user(
            request.user, 'spaces.can_manage_space_bookings', klass=Space  # klass 匹配 Space
        ).values_list('id', flat=True)

        # 获取用户有 'spaces.can_manage_bookable_amenity' 权限的所有 BookableAmenity 对象的 ID
        managed_amenities_ids = get_objects_for_user(
            request.user, 'spaces.can_manage_bookable_amenity', klass=BookableAmenity  # klass 匹配 BookableAmenity
        ).values_list('id', flat=True)

        return qs.filter(
            Q(space__id__in=managed_spaces_ids) | Q(bookable_amenity__id__in=managed_amenities_ids)
        ).select_related('user', 'reviewed_by', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space')

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        return request.user.is_staff and request.user.is_space_manager  # This check doesn't need get_objects_for_user

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        target_space = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        if not (target_space and SPACES_MODELS_LOADED): return False  # Ensure target_space and modules are loaded
        return request.user.has_perm('spaces.can_manage_space_bookings', target_space)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        target_space = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        if not (target_space and SPACES_MODELS_LOADED): return False
        return request.user.has_perm('spaces.can_manage_space_bookings', target_space)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return False  # Space managers cannot bulk delete

        target_space = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        if not (target_space and SPACES_MODELS_LOADED): return False
        return request.user.has_perm('bookings.delete_booking', obj)

    def get_actions(self, request):
        if not request.user.is_authenticated: return {}
        actions = super().get_actions(request)
        if not (request.user.is_superuser or request.user.is_system_admin):
            space_manager_specific_actions = [
                'approve_bookings', 'reject_bookings', 'cancel_bookings', 'mark_completed_bookings',
                'mark_checked_in', 'mark_no_show_and_violate'
            ]
            actions.pop('delete_selected', None)

            all_current_actions = list(actions.keys())
            for action_name in all_current_actions:
                if action_name not in space_manager_specific_actions:
                    actions.pop(action_name, None)

        if 'delete_selected' in actions: del actions['delete_selected']
        return actions

    @admin.action(description="批准选择的预订")
    def approve_bookings(self, request, queryset):
        if not request.user.is_authenticated: self.message_user(request, "您没有权限执行此操作，请先登录。",
                                                                messages.ERROR); return
        approved_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_approve_booking') or \
                    (target_space and request.user.has_perm('spaces.can_manage_space_bookings', target_space)):
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
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_approve_booking') or \
                    (target_space and request.user.has_perm('spaces.can_manage_space_bookings', target_space)):
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
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.change_booking') or \
                    (target_space and request.user.has_perm('spaces.can_manage_space_bookings', target_space)):
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
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_check_in_booking') or \
                    (target_space and request.user.has_perm('spaces.can_manage_space_bookings', target_space)):
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
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_check_in_booking') or \
                    (target_space and request.user.has_perm('spaces.can_manage_space_bookings', target_space)):
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
        from .models import Violation
        no_show_count = 0;
        violation_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_check_in_booking') or \
                    (target_space and request.user.has_perm('spaces.can_manage_space_bookings', target_space)):
                if booking.status in ['PENDING', 'APPROVED'] and booking.end_time < timezone.now():
                    booking.status = 'NO_SHOW'
                    booking.save(update_fields=['status']);
                    no_show_count += 1
                    space_type_for_violation = target_space.space_type if target_space else None
                    if space_type_for_violation:
                        Violation.objects.create(
                            user=booking.user, booking=booking, space_type=space_type_for_violation,
                            violation_type='NO_SHOW',
                            description=f"用户 {booking.user.get_full_name} 未在 {getattr(target_space, 'name', '未知空间')} 预订中签到。",
                            issued_by=request.user, penalty_points=1
                        );
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


# ====================================================================
# Violation Admin (违约记录管理)
# ====================================================================
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
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        if not obj.space_type and obj.booking:
            if obj.booking.space and obj.booking.space.space_type:
                obj.space_type = obj.booking.space.space_type
            elif obj.booking.bookable_amenity and obj.booking.bookable_amenity.space \
                    and obj.booking.bookable_amenity.space.space_type:
                obj.space_type = obj.bookable_amenity.space.space_type

        # 注意：这里管理权限 'spaces.can_manage_space_details' 是针对 Space 模型的，
        # obj.space_type 是 SpaceType 模型。
        # 这里需要更精细的权限检查，如果一个空间管理员管理一个 SpaceType 下的任何一个 Space，
        # 则可以修改该 SpaceType 相关的 Violation。

        # 检查用户是否是超级用户/系统管理员
        if request.user.is_superuser or request.user.is_system_admin:
            # 超级用户和系统管理员拥有所有权限，直接跳过对象权限检查
            pass
        else:
            # 对于空间管理员，检查他们是否有权限通过其管理的 Space 来管理这个 Violation 所属的 SpaceType
            target_space_type_for_perm = obj.space_type
            if target_space_type_for_perm:
                # 尝试获取用户有 'spaces.can_manage_space_details' 权限的所有 Space 对象
                managed_spaces = get_objects_for_user(request.user,
                                                      'spaces.can_manage_space_details',
                                                      klass=Space)
                # 检查是否存在任何一个被管理的 Space 属于这个 Violation 的 SpaceType
                if not managed_spaces.filter(space_type=target_space_type_for_perm).exists():
                    messages.error(request, f"您没有权限修改此违规记录(ID: {obj.pk})，因为您不管理其所属的空间类型。",
                                   messages.ERROR);
                    return
            else:
                # 如果 SpaceType 为空，通常表示全局违规，普通空间管理员无权修改
                messages.error(request, f"您没有权限修改全局违规记录(ID: {obj.pk})。", messages.ERROR);
                return

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
        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'booking__space', 'booking__bookable_amenity__space',
                                     'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type')
        if not SPACES_MODELS_LOADED:
            messages.warning(request, "Space models not available. Violations cannot be filtered by space permissions.")
            return qs.none()

        # CRITICAL FIX: 先获取有权限管理的 Space 对象，再从 Space 中提取 SpaceType ID
        # Permission 'spaces.can_manage_space_details' is defined on Space model, so klass=Space
        managed_spaces = get_objects_for_user(
            request.user, 'spaces.can_manage_space_details', klass=Space
        )
        # 从这些 Space 对象中获取所有关联的 SpaceType 的 ID (去重，并去除 None 值)
        managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
        managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

        # 过滤 Violation 记录，使其与用户管理的 SpaceType 相关联
        # 排除 space_type 为空（全局）的记录，因为空间管理员不应看到全局记录
        return qs.filter(
            Q(space_type__id__in=managed_spacetype_ids) |
            Q(booking__space__space_type__id__in=managed_spacetype_ids) |
            Q(booking__bookable_amenity__space__space_type__id__in=managed_spacetype_ids)
        ).distinct().select_related('user', 'booking__space', 'booking__bookable_amenity__space',
                                    'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type')

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        return request.user.is_staff and request.user.is_space_manager

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission based on SpaceType associated with the Violation
        # If the obj has a space_type, check if the user manages any Space under that SpaceType.
        # Otherwise, if it's a global violation, only superusers/system_admins can view.
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global violation (space_type is None)
            return request.user.is_superuser or request.user.is_system_admin

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin or (
                request.user.is_staff and request.user.is_space_manager)

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission for change, similar to has_view_permission.
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global violation (space_type is None)
            return request.user.is_superuser or request.user.is_system_admin

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser

    def get_actions(self, request):
        if not request.user.is_authenticated: return {}
        actions = super().get_actions(request)

        if not (request.user.is_superuser or request.user.is_system_admin):
            actions.pop('delete_selected', None)
            allowed_actions_for_space_manager = ['mark_resolved']
            all_current_actions = list(actions.keys())
            for action_name in all_current_actions:
                if action_name not in allowed_actions_for_space_manager:
                    actions.pop(action_name, None)

            if 'mark_resolved' not in actions:
                actions['mark_resolved'] = self.get_action('mark_resolved')

        return actions

    @admin.action(description="解决选择的违约记录")
    def mark_resolved(self, request, queryset):
        if not request.user.is_authenticated: self.message_user(request, "您没有权限执行此操作，请先登录。",
                                                                messages.ERROR); return
        resolved_count = 0
        for violation in queryset:
            # 权限检查与 save_model/has_change_permission 类似
            if request.user.is_superuser or request.user.is_system_admin:
                can_resolve = True
            elif violation.space_type:
                managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
                can_resolve = managed_spaces.filter(space_type=violation.space_type).exists()
            else:  # Global violation
                can_resolve = False

            if can_resolve:
                if not violation.is_resolved:
                    violation.is_resolved = True
                    violation.resolved_by = request.user
                    violation.resolved_at = timezone.now()
                    violation.save(update_fields=['is_resolved', 'resolved_by', 'resolved_at'])
                    resolved_count += 1
                else:
                    self.message_user(request, f"违规 {violation.id} 已是解决状态。", messages.WARNING)
            else:
                self.message_user(request, f"您没有权限解决违规 {violation.id}。", messages.ERROR)
        self.message_user(request, f"成功解决了 {resolved_count} 条违约记录。", messages.SUCCESS)


# ====================================================================
# UserPenaltyPointsPerSpaceType Admin (用户违约点数管理)
# ====================================================================
@admin.register(UserPenaltyPointsPerSpaceType)
class UserPenaltyPointsPerSpaceTypeAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'user_display', 'space_type_display', 'current_penalty_points',
        'last_violation_at', 'last_ban_trigger_at', 'updated_at'
    )
    list_filter = ('space_type', 'current_penalty_points', 'user')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'space_type__name')
    date_hierarchy = 'updated_at'
    raw_id_fields = ('user', 'space_type')
    fieldsets = (
        (None, {'fields': ('user', 'space_type', 'current_penalty_points')}),
        ('时间信息', {'fields': ('last_violation_at', 'last_ban_trigger_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ('user', 'space_type', 'current_penalty_points', 'last_violation_at', 'last_ban_trigger_at',
                       'updated_at')

    @admin.display(description='用户')
    def user_display(self, obj: 'UserPenaltyPointsPerSpaceType'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'UserPenaltyPointsPerSpaceType'):
        return obj.space_type.name if obj.space_type else '全局'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'space_type')

        if not SPACES_MODELS_LOADED:
            messages.warning(request,
                             "Space models not available. Penalty points cannot be filtered by space permissions.")
            return qs.none()

        # CRITICAL FIX: 先获取有权限管理的 Space 对象，再从 Space 中提取 SpaceType ID
        managed_spaces = get_objects_for_user(
            request.user, 'spaces.can_manage_space_details', klass=Space
        )
        managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
        managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

        # 过滤 UserPenaltyPointsPerSpaceType 记录，排除 space_type 为空（全局）的记录
        return qs.filter(space_type__id__in=managed_spacetype_ids).select_related('user', 'space_type')

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin or \
            (request.user.is_staff and request.user.is_space_manager)

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission based on SpaceType
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global penalty_points
            return request.user.is_superuser or request.user.is_system_admin

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ====================================================================
# SpaceTypeBanPolicy Admin (空间类型禁用策略管理)
# ====================================================================
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

        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('space_type')

        # CRITICAL FIX: 空间管理员应该只看到他们管理的 SpaceType 相关的禁用策略
        if request.user.is_staff and request.user.is_space_manager:
            if not SPACES_MODELS_LOADED:
                messages.warning(request,
                                 "Space models not available. Ban policies cannot be filtered by space permissions.")
                return qs.none()

            managed_spaces = get_objects_for_user(
                request.user, 'spaces.can_manage_space_details', klass=Space  # klass 匹配 Space
            )
            managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
            managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

            # 过滤 SpaceTypeBanPolicy 记录，排除 space_type 为空（全局）的记录
            return qs.filter(space_type__id__in=managed_spacetype_ids).select_related('space_type')

        return qs.none()  # Default for other non-staff users

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # 视图权限：如果是一个具体的策略，检查其 SpaceType 是否被当前用户管理
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # 全局策略
            return request.user.is_superuser or request.user.is_system_admin

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin


# ====================================================================
# UserSpaceTypeBan Admin (用户禁用记录管理)
# ====================================================================
@admin.register(UserSpaceTypeBan)
class UserSpaceTypeBanAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'user_display', 'space_type_display', 'start_date', 'end_date',
        'is_active', 'ban_policy_applied_display', 'reason', 'issued_by_display', 'issued_at'
    )
    list_filter = ('space_type', 'issued_at', 'user', 'issued_by')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'reason', 'space_type__name')
    date_hierarchy = 'issued_at'
    raw_id_fields = ('user', 'space_type', 'ban_policy_applied', 'issued_by')
    fieldsets = (
        (None, {'fields': ('user', 'space_type', ('start_date', 'end_date'), 'reason',)}),
        ('策略与记录', {'fields': ('ban_policy_applied', ('issued_by', 'issued_at'))}),
    )
    readonly_fields = ('issued_at', 'issued_by')

    def save_model(self, request, obj, form, change):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return

        # 权限检查与 ViolationAdmin 类似
        if request.user.is_superuser or request.user.is_system_admin:
            pass  # 超级用户和系统管理员拥有所有权限
        else:
            target_space_type_for_perm = obj.space_type
            if target_space_type_for_perm:
                managed_spaces = get_objects_for_user(request.user,
                                                      'spaces.can_manage_space_details',
                                                      klass=Space)
                if not managed_spaces.filter(space_type=target_space_type_for_perm).exists():
                    messages.error(request, f"您没有权限修改此禁用记录(ID: {obj.pk})，因为您不管理其所属的空间类型。",
                                   messages.ERROR);
                    return
            else:
                messages.error(request, f"您没有权限修改全局禁用记录(ID: {obj.pk})。", messages.ERROR);
                return

        if not obj.issued_by and isinstance(request.user, CustomUser): obj.issued_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='用户')
    def user_display(self, obj: 'UserSpaceTypeBan'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'UserSpaceTypeBan'):
        return obj.space_type.name if obj.space_type else '全局'

    @admin.display(description='策略')
    def ban_policy_applied_display(self, obj: 'UserSpaceTypeBan'):
        return str(obj.ban_policy_applied) if obj.ban_policy_applied else 'N/A'

    @admin.display(description='执行人员')
    def issued_by_display(self, obj: 'UserSpaceTypeBan'):
        return obj.issued_by.get_full_name if obj.issued_by else '系统自动'

    @admin.display(boolean=True, description='是否活跃')
    def is_active(self, obj: 'UserSpaceTypeBan'):
        return obj.end_date > timezone.now() if obj.end_date else True

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'space_type', 'ban_policy_applied', 'issued_by')

        # CRITICAL FIX: 先获取有权限管理的 Space 对象，再从 Space 中提取 SpaceType ID
        managed_spaces = get_objects_for_user(
            request.user, 'spaces.can_manage_space_details', klass=Space
        )
        managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
        managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

        # 过滤 UserSpaceTypeBan 记录，排除 space_type 为空（全局）的记录
        return qs.filter(space_type__id__in=managed_spacetype_ids).select_related('user', 'space_type',
                                                                                  'ban_policy_applied', 'issued_by')

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin or \
            (request.user.is_staff and request.user.is_space_manager)

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission based on SpaceType
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global ban
            return request.user.is_superuser or request.user.is_system_admin

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission for change
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global ban
            return request.user.is_superuser or request.user.is_system_admin  # only superuser/system_admin can change global ban

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin


# ====================================================================
# UserSpaceTypeExemption Admin (用户豁免记录管理)
# ====================================================================
@admin.register(UserSpaceTypeExemption)
class UserSpaceTypeExemptionAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'user_display', 'space_type_display', 'exemption_reason',
        'start_date', 'end_date', 'is_active', 'granted_by_display', 'granted_at'
    )
    list_filter = ('space_type', 'granted_at', 'user', 'granted_by')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'exemption_reason', 'space_type__name')
    date_hierarchy = 'granted_at'
    raw_id_fields = ('user', 'space_type', 'granted_by')
    fieldsets = (
        (None, {'fields': ('user', 'space_type', ('start_date', 'end_date'), 'exemption_reason',)}),
        ('授权信息', {'fields': (('granted_by', 'granted_at'),)}),
    )
    readonly_fields = ('granted_at',)

    def save_model(self, request, obj, form, change):
        if not request.user.is_authenticated: messages.error(request, "您没有权限执行此操作，请先登录。",
                                                             messages.ERROR); return
        # 权限检查与 ViolationAdmin 类似
        if request.user.is_superuser or request.user.is_system_admin:
            pass  # 超级用户和系统管理员拥有所有权限
        else:
            target_space_type_for_perm = obj.space_type
            if target_space_type_for_perm:
                managed_spaces = get_objects_for_user(request.user,
                                                      'spaces.can_manage_space_details',
                                                      klass=Space)
                if not managed_spaces.filter(space_type=target_space_type_for_perm).exists():
                    messages.error(request, f"您没有权限修改此豁免记录(ID: {obj.pk})，因为您不管理其所属的空间类型。",
                                   messages.ERROR);
                    return
            else:
                messages.error(request, f"您没有权限修改全局豁免记录(ID: {obj.pk})。", messages.ERROR);
                return

        if not obj.granted_by and isinstance(request.user, CustomUser): obj.granted_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='用户')
    def user_display(self, obj: 'UserSpaceTypeExemption'):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'UserSpaceTypeExemption'):
        return obj.space_type.name if obj.space_type else '全局'

    @admin.display(description='授权人员')
    def granted_by_display(self, obj: 'UserSpaceTypeExemption'):
        return obj.granted_by.get_full_name if obj.granted_by else 'N/A'

    @admin.display(boolean=True, description='是否活跃')
    def is_active(self, obj: 'UserSpaceTypeExemption'):
        if obj.start_date is None and obj.end_date is None: return True
        if obj.end_date is None and obj.start_date is not None and obj.start_date <= timezone.now(): return True
        return obj.start_date <= timezone.now() < obj.end_date if obj.start_date and obj.end_date else False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated: return qs.none()
        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'space_type', 'granted_by')

        # CRITICAL FIX: 先获取有权限管理的 Space 对象，再从 Space 中提取 SpaceType ID
        managed_spaces = get_objects_for_user(
            request.user, 'spaces.can_manage_space_details', klass=Space
        )
        managed_spacetype_ids = list(managed_spaces.values_list('space_type__id', flat=True).distinct())
        managed_spacetype_ids = [id for id in managed_spacetype_ids if id is not None]

        # 过滤 UserSpaceTypeExemption 记录，排除 space_type 为空（全局）的记录
        return qs.filter(space_type__id__in=managed_spacetype_ids).select_related('user', 'space_type', 'granted_by')

    def has_module_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin or \
            (request.user.is_staff and request.user.is_space_manager)

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission based on SpaceType
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global exemption
            return request.user.is_superuser or request.user.is_system_admin

    def has_add_permission(self, request):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        if request.user.is_superuser or request.user.is_system_admin: return True
        if obj is None: return self.has_module_permission(request)  # For list view, defer to module permission

        # Check object-level permission for change
        if obj.space_type:
            managed_spaces = get_objects_for_user(request.user, 'spaces.can_manage_space_details', klass=Space)
            return managed_spaces.filter(space_type=obj.space_type).exists()
        else:  # Global exemption
            return request.user.is_superuser or request.user.is_system_admin

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_authenticated: return False
        return request.user.is_superuser or request.user.is_system_admin