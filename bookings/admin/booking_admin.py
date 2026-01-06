# bookings/admin/booking_admin.py
from django.contrib import admin
from django.db import transaction, models
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, Manager, QuerySet

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

from django.conf import settings
CustomUser = settings.AUTH_USER_MODEL

# 直接导入本应用的模型
from bookings.models import Booking # 注意：这里是 bookings.models.Booking，不是 .models
from bookings.models import Violation # 导入 Violation for mark_no_show_and_violate

# 确保 SPACES_MODELS_LOADED 标志存在
SPACES_MODELS_LOADED = False
try:
    from spaces.models import Space, SpaceType, BookableAmenity
    SPACES_MODELS_LOADED = True
except ImportError:
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
    print("Warning: Missing modules from 'spaces' app. Using robust mock objects in bookings/admin.py. Functionality may be limited.")

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
        # from .models import Violation # Violation 已经在顶部导入了
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