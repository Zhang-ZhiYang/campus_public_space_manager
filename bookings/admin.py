# bookings/admin.py
from django.contrib import admin
from django.db import transaction
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q  # , Case, When, Value, BooleanField # 移除不必要的导入，因为 SpaceTypeBanPolicyAdmin 不再需要它们

# 直接导入本应用的模型
from .models import (
    Booking,
    Violation,
    UserPenaltyPointsPerSpaceType,
    SpaceTypeBanPolicy,
    UserSpaceTypeBan,
    UserSpaceTypeExemption
)

# 导入 GuardedModelAdmin
from guardian.admin import GuardedModelAdmin

# 导入 CustomUser 和 Space 相关模型
from django.conf import settings

CustomUser = settings.AUTH_USER_MODEL

# 确保 SPACES_MODELS_LOADED 标志存在
SPACES_MODELS_LOADED = False
try:
    from spaces.models import Space, SpaceType, BookableAmenity

    SPACES_MODELS_LOADED = True
except ImportError:
    # 强化 Mock 对象，确保它们有 .objects 属性和相关的 queryset 方法
    class MockSpaceObjects:
        def for_user(self, user, perm, klass=None): return self.none()

        def none(self): return []  # 返回一个空列表，避免 TypeError

        def values_list(self, *args, **kwargs): return []  # 返回空列表

        def filter(self, *args, **kwargs): return self.none()  # 允许filter操作


    class Space:
        name = "Mock Space"
        requires_approval = False
        space_type = None  # Mock this for safety
        objects = MockSpaceObjects()  # 绑定 mock objects

        def __str__(self): return self.name

        def __init__(self, *args, **kwargs): pass


    class MockSpaceTypeObjects:
        def for_user(self, user, perm, klass=None): return self.none()

        def none(self): return []

        def values_list(self, *args, **kwargs): return []

        def filter(self, *args, **kwargs): return self.none()


    class SpaceType:
        name = "Mock SpaceType"
        spaces = Space.objects  # Ensure mock SpaceType can link to mock Space
        objects = MockSpaceTypeObjects()  # 绑定 mock objects

        def __str__(self): return self.name

        def __init__(self, *args, **kwargs): pass


    class MockBookableAmenityObjects:
        def for_user(self, user, perm, klass=None): return self.none()

        def none(self): return []

        def values_list(self, *args, **kwargs): return []

        def filter(self, *args, **kwargs): return self.none()


    class BookableAmenity:
        amenity = SpaceType()  # Use Mock SpaceType
        space = Space()  # Use Mock Space
        objects = MockBookableAmenityObjects()  # 绑定 mock objects

        def __str__(self): return "Mock BookableAmenity"

        def __init__(self, *args, **kwargs): pass


    print(
        "Warning: Missing modules from 'spaces' app. Using robust mock objects in bookings/admin.py. Functionality may be limited.")


# ====================================================================
# Booking Admin (预订管理)
# ====================================================================
@admin.register(Booking)
class BookingAdmin(GuardedModelAdmin):  # 继承 GuardedModelAdmin
    list_display = (
        'id', 'user_display', 'booking_target_display', 'status', 'booked_quantity',
        'start_time', 'end_time', 'reviewed_by_display', 'requires_approval_status'
    )
    list_filter = (
        'status', 'start_time', 'end_time',
        'space__space_type',  # 按空间类型过滤
        'space',  # 按具体空间过滤
        'bookable_amenity__amenity',  # 按设施类型过滤
        'user',  # 按用户过滤
        'reviewed_by'  # 按审核人员过滤
    )
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name', 'purpose',
        'space__name', 'bookable_amenity__space__name',
        'bookable_amenity__amenity__name'  # 增加搜索字段
    )
    raw_id_fields = ('user', 'space', 'bookable_amenity', 'reviewed_by')
    date_hierarchy = 'start_time'

    fieldsets = (
        (None, {
            'fields': (('user', 'status'), ('space', 'bookable_amenity', 'booked_quantity'), 'purpose',)
        }),
        ('时间信息', {
            'fields': (('start_time', 'end_time'),)
        }),
        ('审核信息', {
            'fields': ('admin_notes', ('reviewed_by', 'reviewed_at'))
        }),
        ('系统信息', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
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
        # ⚠️ 强化对 None 的检查，防止深层 AttributeErrors 或 TypeError
        if obj.bookable_amenity:
            # 访问 amenity 和 space 前进一步检查是否存在
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

    requires_approval_status.boolean = True  # 显示为勾选框

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return qs.none()

            # 检查 spaces 模型是否有效
        if not SPACES_MODELS_LOADED:
            messages.warning(request, "Space models not available. Bookings cannot be filtered by space permissions.")
            if request.user.is_superuser or request.user.is_system_admin:
                return qs.select_related(
                    'user', 'reviewed_by', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
                )
            return qs.none()  # 非管理员且模型未加载，无法确定权限，返回空

        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related(
                'user', 'reviewed_by', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
            )

        # 空间管理员只看到他们有 'can_manage_space_bookings' 权限的 Space 相关的 Booking
        # 使用 Space.objects 确保调用 mock 或真实 manager
        managed_spaces_ids = Space.objects.for_user(request.user, 'spaces.can_manage_space_bookings').values_list('id',
                                                                                                                  flat=True)

        # 同样，也可能直接有 BookableAmenity 的管理权限
        managed_amenities_ids = BookableAmenity.objects.for_user(request.user,
                                                                 'spaces.can_manage_bookable_amenity').values_list('id',
                                                                                                                   flat=True)

        return qs.filter(
            Q(space__id__in=managed_spaces_ids) |
            Q(bookable_amenity__id__in=managed_amenities_ids)
        ).select_related(
            'user', 'reviewed_by', 'space', 'bookable_amenity__amenity', 'bookable_amenity__space'
        )

    # --- 权限检查方法 (使用 django-guardian 对象级权限) ---
    def has_module_permission(self, request):
        """
        模块级权限：查看 Booking 列表页的权限。
        系统管理员和空间管理员 (通过 is_staff 判断) 且至少有查看 Booking 的模型级权限
        或有任何 Space 对象的managing_space_bookings 权限。
        """
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        return request.user.is_staff and (request.user.has_perm('bookings.view_booking') or \
                                          (SPACES_MODELS_LOADED and request.user.has_perm(
                                              'spaces.can_manage_space_bookings')))

    def has_view_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:  # 检查模型级权限
            return request.user.is_space_manager or request.user.has_perm('bookings.view_booking')

        # 检查对象级权限: 判断用户是否有权限查看此具体 booking
        if not SPACES_MODELS_LOADED: return False  # 如果模型未加载，无法判断对象权限

        target_space = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        if target_space:
            return request.user.has_perm('spaces.can_manage_space_bookings', target_space)
        return False

    def has_add_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        # 通常不允许在Admin中手动添加 Booking，而是通过前端
        return False

    def has_change_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:  # 检查模型级权限 (能否访问修改列表)
            return request.user.is_space_manager or request.user.has_perm('bookings.change_booking')

        # 检查对象级权限: 判断用户是否有权限修改此具体 booking
        if not SPACES_MODELS_LOADED: return False  # 如果模型未加载，无法判断对象权限

        target_space = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        if target_space:
            return request.user.has_perm('spaces.can_manage_space_bookings', target_space)
        return False

    def has_delete_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:  # 检查模型级权限 (能否看到删除选项)
            return request.user.is_space_manager or request.user.has_perm('bookings.delete_booking')

        # 检查对象级权限: 判断用户是否有权限删除此具体 booking
        if not SPACES_MODELS_LOADED: return False  # 如果模型未加载，无法判断对象权限

        target_space = obj.space or (obj.bookable_amenity.space if obj.bookable_amenity else None)
        if target_space:
            return request.user.has_perm('spaces.can_manage_space_bookings', target_space)
        return False

    def get_actions(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return {}  # 未认证用户无任何 action

        actions = super().get_actions(request)

        # 根据用户权限动态移除或添加 action
        can_approve_global = request.user.is_superuser or request.user.is_system_admin or \
                             request.user.has_perm('bookings.can_approve_booking')

        can_checkin_global = request.user.is_superuser or request.user.is_system_admin or \
                             request.user.has_perm('bookings.can_check_in_booking')

        specific_booking_actions = []
        if request.user.is_space_manager:
            specific_booking_actions.extend(
                ['approve_bookings', 'reject_bookings', 'mark_checked_in', 'mark_completed_bookings',
                 'mark_no_show_and_violate'])

        if not can_approve_global:
            actions.pop('approve_bookings', None)
            actions.pop('reject_bookings', None)
        if not can_checkin_global:
            actions.pop('mark_checked_in', None)
            actions.pop('mark_completed_bookings', None)
            actions.pop('mark_no_show_and_violate', None)

        if request.user.is_space_manager:
            for action_name in specific_booking_actions:
                if action_name not in actions:
                    actions[action_name] = self.get_action(action_name)

        if 'delete_selected' in actions and not self.has_delete_permission(request):
            del actions['delete_selected']

        return actions

    # --- Action 方法定义 ---
    # 所有 Action 方法内部都已添加了对象级权限校验

    @admin.action(description="批准选择的预订")
    def approve_bookings(self, request, queryset):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        approved_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            # 只有系统管理员/有全局批准权限的用户 或 有此空间管理权限的空间管理员 才能操作
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_approve_booking') or \
                    (SPACES_MODELS_LOADED and target_space and request.user.has_perm('spaces.can_manage_space_bookings',
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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        rejected_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_approve_booking') or \
                    (SPACES_MODELS_LOADED and target_space and request.user.has_perm('spaces.can_manage_space_bookings',
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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        cancelled_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.change_booking') or \
                    (SPACES_MODELS_LOADED and target_space and request.user.has_perm('spaces.can_manage_space_bookings',
                                                                                     target_space)):

                if booking.status in ['PENDING', 'APPROVED', 'CHECKED_IN']:  # 允许取消这几个状态
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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        completed_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_check_in_booking') or \
                    (SPACES_MODELS_LOADED and target_space and request.user.has_perm('spaces.can_manage_space_bookings',
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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        checked_in_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_check_in_booking') or \
                    (SPACES_MODELS_LOADED and target_space and request.user.has_perm('spaces.can_manage_space_bookings',
                                                                                     target_space)):

                if booking.status in ['APPROVED', 'PENDING']:  # PENDING 也可以签到，但通常会先批准
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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        from .models import Violation  # 导入 Violation 模型
        no_show_count = 0
        violation_count = 0
        for booking in queryset:
            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_check_in_booking') or \
                    (SPACES_MODELS_LOADED and target_space and request.user.has_perm('spaces.can_manage_space_bookings',
                                                                                     target_space)):

                if booking.status in ['PENDING', 'APPROVED'] and booking.end_time < timezone.now():
                    booking.status = 'NO_SHOW'
                    booking.save(update_fields=['status'])
                    no_show_count += 1

                    space_type_for_violation = target_space.space_type if target_space else None
                    if space_type_for_violation:
                        Violation.objects.create(
                            user=booking.user,
                            booking=booking,
                            space_type=space_type_for_violation,
                            violation_type='NO_SHOW',
                            description=f"用户 {booking.user.get_full_name} 未在 {getattr(target_space, 'name', '未知空间')} 预订中签到。",
                            issued_by=request.user,
                            penalty_points=1  # 默认1点
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


# ====================================================================
# Violation Admin (违约记录管理)
# ====================================================================
@admin.register(Violation)
class ViolationAdmin(GuardedModelAdmin):  # 继承 GuardedModelAdmin
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
        (None, {
            'fields': (
                ('user', 'booking'),
                'violation_type',
                'space_type',
                'description',
                ('penalty_points', 'is_resolved')
            )
        }),
        ('记录与解决信息', {
            'fields': (('issued_by', 'issued_at'), ('resolved_by', 'resolved_at'))
        }),
    )
    readonly_fields = ('issued_at',)

    def save_model(self, request, obj: 'Violation', form, change):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            messages.error(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        # 自动填充解决信息
        if obj.is_resolved and not obj.resolved_at:
            obj.resolved_at = timezone.now()
            obj.resolved_by = request.user if isinstance(request.user, CustomUser) else None
        elif not obj.is_resolved and obj.resolved_at:  # 如果从已解决变回未解决，清空解决信息
            obj.resolved_at = None
            obj.resolved_by = None

        # 确保 space_type 字段在 save_model 之前被正确赋值 (如果预订存在)
        if not obj.space_type and obj.booking:
            if obj.booking.space and obj.booking.space.space_type:
                obj.space_type = obj.booking.space.space_type
            elif obj.booking.bookable_amenity and obj.booking.bookable_amenity.space \
                    and obj.booking.bookable_amenity.space.space_type:
                obj.space_type = obj.booking.bookable_amenity.space.space_type

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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return qs.none()

        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related(
                'user', 'booking__space', 'booking__bookable_amenity__space',
                'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type'
            )

        if not SPACES_MODELS_LOADED:
            messages.warning(request, "Space models not available. Violations cannot be filtered by space permissions.")
            return qs.none()

            # 空间管理员只看到他们有管理权限 Space 相关的 Violation
        managed_spaces_ids = Space.objects.for_user(request.user, 'spaces.can_manage_space_details').values_list('id',
                                                                                                                 flat=True)

        return qs.filter(
            Q(space_type__spaces__id__in=managed_spaces_ids) |  # 违规直接关联 space_type，且该 space_type 下有管理的 space
            Q(booking__space__id__in=managed_spaces_ids) |
            Q(booking__bookable_amenity__space__id__in=managed_spaces_ids)
        ).distinct().select_related(
            'user', 'booking__space', 'booking__bookable_amenity__space',
            'booking__bookable_amenity__amenity', 'issued_by', 'resolved_by', 'space_type'
        )

    # --- 权限检查方法 (使用 django-guardian 对象级权限) ---
    def has_module_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        return request.user.is_staff and (request.user.has_perm('bookings.view_violation') or \
                                          (SPACES_MODELS_LOADED and request.user.has_perm(
                                              'spaces.can_manage_space_details')))

    def has_view_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:  # 模型级权限
            return request.user.is_space_manager or request.user.has_perm('bookings.view_violation')

        # 对象级权限: 判断用户是否有权限查看此具体 violation
        if not SPACES_MODELS_LOADED: return False

        target_space_type = obj.space_type or \
                            (obj.booking.space.space_type if obj.booking and obj.booking.space else None)
        if target_space_type:
            return request.user.has_perm('spaces.can_manage_space_details', target_space_type)  # 需要 SpaceType 的管理权限
        return False

    def has_add_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin or request.user.is_space_manager

    def has_change_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:  # 模型级权限
            return request.user.is_space_manager or request.user.has_perm('bookings.change_violation')

        # 对象级权限: 判断用户是否有权限修改此具体 violation (特别是解决违规)
        if not SPACES_MODELS_LOADED: return False

        target_space_type = obj.space_type or \
                            (obj.booking.space.space_type if obj.booking and obj.booking.space else None)
        if target_space_type:
            return request.user.has_perm('spaces.can_manage_space_details', target_space_type)
        return False

    def has_delete_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:  # 模型级权限
            return request.user.has_perm('bookings.delete_violation')

        # 空间管理员通常不应删除违规，因为违规记录很重要
        return False

    def get_actions(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return {}

        actions = super().get_actions(request)

        can_resolve_global = request.user.is_superuser or request.user.is_system_admin or \
                             request.user.has_perm('bookings.can_resolve_violation')

        if not can_resolve_global and request.user.is_space_manager:
            pass  # 空间管理员即使没有全局权限，也可以通过对象权限行使 'mark_resolved'
        elif not can_resolve_global and not request.user.is_space_manager:
            actions.pop('mark_resolved', None)

        if 'delete_selected' in actions and not self.has_delete_permission(request):
            del actions['delete_selected']

        return actions

    @admin.action(description="解决选择的违约记录")
    def mark_resolved(self, request, queryset):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        resolved_count = 0
        for violation in queryset:
            target_space_type = violation.space_type or \
                                (
                                    violation.booking.space.space_type if violation.booking and violation.booking.space else None)

            # 权限检查：系统管理员/有全局解决权限 或 有对应 SpaceType 管理权限的空间管理员
            if request.user.is_superuser or request.user.is_system_admin or \
                    request.user.has_perm('bookings.can_resolve_violation') or \
                    (SPACES_MODELS_LOADED and target_space_type and request.user.has_perm(
                        'spaces.can_manage_space_details', target_space_type)):

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
class UserPenaltyPointsPerSpaceTypeAdmin(GuardedModelAdmin):  # 继承 GuardedModelAdmin
    list_display = (
        'id', 'user_display', 'space_type_display', 'current_penalty_points',
        'last_violation_at', 'last_ban_trigger_at', 'updated_at'
    )
    list_filter = ('space_type', 'current_penalty_points', 'user')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'space_type__name')
    date_hierarchy = 'updated_at'
    raw_id_fields = ('user', 'space_type')

    fieldsets = (
        (None, {
            'fields': ('user', 'space_type', 'current_penalty_points')
        }),
        ('时间信息', {
            'fields': ('last_violation_at', 'last_ban_trigger_at', 'updated_at'),
            'classes': ('collapse',)
        }),
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
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return qs.none()

        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'space_type')

        if not SPACES_MODELS_LOADED:
            messages.warning(request,
                             "Space models not available. Penalty points cannot be filtered by space permissions.")
            return qs.none()

            # 空间管理员只看他们有管理权限 SpaceType 相关的点数记录
        managed_spacetypes_ids = SpaceType.objects.for_user(request.user,
                                                            'spaces.can_manage_space_details').values_list('id',
                                                                                                           flat=True)
        return qs.filter(space_type__id__in=managed_spacetypes_ids).select_related('user', 'space_type')

    def has_module_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_staff and (request.user.is_system_admin or request.user.has_perm(
            'bookings.view_userpenaltypointspperspacetype') or \
                                          (SPACES_MODELS_LOADED and request.user.has_perm(
                                              'spaces.can_manage_space_details')))

    def has_view_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:  # 模型级权限
            return request.user.is_space_manager or request.user.has_perm(
                'bookings.view_userpenaltypointspperspacetype')

        # 对象级权限: 空间管理员有权查看其管理空间类型下的用户点数
        if not SPACES_MODELS_LOADED: return False
        return request.user.is_space_manager and obj.space_type and \
            request.user.has_perm('spaces.can_manage_space_details', obj.space_type)

    def has_add_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return False  # 不允许手动添加，由系统自动生成

    def has_change_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return False  # 不允许手动修改

    def has_delete_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return False  # 不允许手动删除


# ====================================================================
# SpaceTypeBanPolicy Admin (空间类型禁用策略管理)
# ====================================================================
@admin.register(SpaceTypeBanPolicy)
class SpaceTypeBanPolicyAdmin(GuardedModelAdmin):  # 继承 GuardedModelAdmin
    list_display = (
        'id', 'space_type_display', 'threshold_points', 'ban_duration',
        'priority', 'is_active', 'description'
    )
    list_filter = ('is_active', 'space_type', 'priority')
    search_fields = ('description', 'space_type__name')
    raw_id_fields = ('space_type',)

    fieldsets = (
        (None, {
            'fields': ('space_type', ('threshold_points', 'ban_duration'), 'priority', 'is_active', 'description')
        }),
        ('系统信息', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    readonly_fields = ('created_at', 'updated_at')

    # ⚠️ 移除自定义的 ordering 属性，让 Django 使用默认排序
    # ordering = ['-is_global', 'space_type__name', '-threshold_points', '-priority']

    @admin.display(description='空间类型')
    def space_type_display(self, obj: 'SpaceTypeBanPolicy'):
        return obj.space_type.name if obj.space_type else '全局'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return qs.none()

            # ⚠️ 移除之前用于创建 'is_global' 字段的 annotate 部分
        return qs.select_related('space_type')

    def has_module_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_view_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_add_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_delete_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin


# ====================================================================
# UserSpaceTypeBan Admin (用户禁用记录管理)
# ====================================================================
@admin.register(UserSpaceTypeBan)
class UserSpaceTypeBanAdmin(GuardedModelAdmin):  # 继承 GuardedModelAdmin
    list_display = (
        'id', 'user_display', 'space_type_display', 'start_date', 'end_date',
        'is_active', 'ban_policy_applied_display', 'reason', 'issued_by_display', 'issued_at'
    )
    list_filter = ('space_type', 'issued_at', 'user', 'issued_by')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'reason', 'space_type__name')
    date_hierarchy = 'issued_at'
    raw_id_fields = ('user', 'space_type', 'ban_policy_applied', 'issued_by')

    fieldsets = (
        (None, {
            'fields': ('user', 'space_type', ('start_date', 'end_date'), 'reason',)
        }),
        ('策略与记录', {
            'fields': ('ban_policy_applied', ('issued_by', 'issued_at'))
        }),
    )
    readonly_fields = ('issued_at', 'issued_by')  # Issued_by 在创建时自动设置

    def save_model(self, request, obj, form, change):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            messages.error(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        # 确保 issued_by 在创建时自动设置
        if not obj.issued_by and isinstance(request.user, CustomUser):
            obj.issued_by = request.user
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
        return obj.end_date > timezone.now() if obj.end_date else True  # 如果没有结束日期，视作永久活跃 (或需要更精细定义)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return qs.none()

        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'space_type', 'ban_policy_applied', 'issued_by')

        if not SPACES_MODELS_LOADED:
            messages.warning(request, "Space models not available. User bans cannot be filtered by space permissions.")
            return qs.none()

            # 空间管理员只看他们有管理权限 SpaceType 相关的禁用记录
        managed_spacetypes_ids = SpaceType.objects.for_user(request.user,
                                                            'spaces.can_manage_space_details').values_list('id',
                                                                                                           flat=True)
        return qs.filter(space_type__id__in=managed_spacetypes_ids).select_related('user', 'space_type',
                                                                                   'ban_policy_applied', 'issued_by')

    def has_module_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_staff and (
                request.user.is_system_admin or request.user.has_perm('bookings.view_userspacetypeban') or \
                (SPACES_MODELS_LOADED and request.user.has_perm('spaces.can_manage_space_details')))

    def has_view_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:
            return request.user.is_space_manager or request.user.has_perm('bookings.view_userspacetypeban')

        if not SPACES_MODELS_LOADED: return False

        target_space_type = obj.space_type
        if target_space_type:
            return request.user.is_space_manager and \
                request.user.has_perm('spaces.can_manage_space_details', target_space_type)
        return False

    def has_add_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:
            return request.user.is_system_admin or request.user.has_perm('bookings.change_userspacetypeban')

        if not SPACES_MODELS_LOADED: return False

        target_space_type = obj.space_type
        if target_space_type:
            return request.user.is_system_admin and \
                request.user.has_perm('spaces.can_manage_space_details', target_space_type)
        return False

    def has_delete_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin


# ====================================================================
# UserSpaceTypeExemption Admin (用户豁免记录管理)
# ====================================================================
@admin.register(UserSpaceTypeExemption)
class UserSpaceTypeExemptionAdmin(GuardedModelAdmin):  # 继承 GuardedModelAdmin
    list_display = (
        'id', 'user_display', 'space_type_display', 'exemption_reason',
        'start_date', 'end_date', 'is_active', 'granted_by_display', 'granted_at'
    )
    list_filter = ('space_type', 'granted_at', 'user', 'granted_by')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'exemption_reason', 'space_type__name')
    date_hierarchy = 'granted_at'
    raw_id_fields = ('user', 'space_type', 'granted_by')

    fieldsets = (
        (None, {
            'fields': ('user', 'space_type', ('start_date', 'end_date'), 'exemption_reason',)
        }),
        ('授权信息', {
            'fields': (('granted_by', 'granted_at'),)
        }),
    )
    readonly_fields = ('granted_at',)

    def save_model(self, request, obj, form, change):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            messages.error(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return

        # 确保 granted_by 在创建时自动设置
        if not obj.granted_by and isinstance(request.user, CustomUser):
            obj.granted_by = request.user
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
        if obj.start_date is None and obj.end_date is None:
            return True  # 永久活跃
        if obj.end_date is None and obj.start_date is not None and obj.start_date <= timezone.now():
            return True  # 从开始日期起永久活跃
        return obj.start_date <= timezone.now() < obj.end_date if obj.start_date and obj.end_date else False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return qs.none()

        if request.user.is_superuser or request.user.is_system_admin:
            return qs.select_related('user', 'space_type', 'granted_by')

        if not SPACES_MODELS_LOADED:
            messages.warning(request, "Space models not available. Exemptions cannot be filtered by space permissions.")
            return qs.none()

            # 空间管理员只看到他们有管理权限 SpaceType 相关的豁免记录
        managed_spacetypes_ids = Space.objects.for_user(request.user, 'spaces.can_manage_space_details').values_list(
            'id', flat=True)
        return qs.filter(space_type__id__in=managed_spacetypes_ids).select_related('user', 'space_type', 'granted_by')

    def has_module_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_staff and (
                request.user.is_system_admin or request.user.has_perm('bookings.view_userspacetypeexemption') or \
                (SPACES_MODELS_LOADED and request.user.has_perm('spaces.can_manage_space_details')))

    def has_view_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:
            return request.user.is_space_manager or request.user.has_perm('bookings.view_userspacetypeexemption')

        if not SPACES_MODELS_LOADED: return False

        target_space_type = obj.space_type
        if target_space_type:
            return request.user.is_space_manager and \
                request.user.has_perm('spaces.can_manage_space_details', target_space_type)
        return False

    def has_add_permission(self, request):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_system_admin:
            return True
        if obj is None:
            return request.user.is_system_admin or request.user.has_perm('bookings.change_userspacetypeexemption')

        if not SPACES_MODELS_LOADED: return False

        target_space_type = obj.space_type
        if target_space_type:
            return request.user.is_system_admin and \
                request.user.has_perm('spaces.can_manage_space_details', target_space_type)
        return False

    def has_delete_permission(self, request, obj=None):
        # ⚠️ 必须的认证检查
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or request.user.is_system_admin