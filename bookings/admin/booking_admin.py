# bookings/admin/booking_admin.py (终极修正版 - 解决Admin Action IntegrityError，并优化权限控制)
from django.contrib import admin
from django.db import transaction, models, IntegrityError  # 导入 IntegrityError
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, Manager, QuerySet
from django.core.exceptions import ValidationError

from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import get_objects_for_user

from django.conf import settings

CustomUser = settings.AUTH_USER_MODEL

from bookings.models import Booking  # 确保这里是真实的 Booking 模型
from bookings.models import Violation  # 真实的 Violation 模型

# 确保 spaces 模块已正确安装和配置，否则需要处理 ImportError
# 生产环境通常不使用 Mock 对象，直接导入即可
try:
    from spaces.models import Space, SpaceType, BookableAmenity

    SPACES_MODELS_LOADED = True
except ImportError:
    # 如果真的无法导入，这里可以留一个警告，但 Admin 的大部分功能会受影响
    # 在生产环境中，这意味着配置问题
    SPACES_MODELS_LOADED = False


    class Space:  # 提供一个极简的 Mock，防止名称错误，但不提供功能
        def __init__(self): self.name = "Mock Space"


    class SpaceType:
        def __init__(self): self.name = "Mock SpaceType"


    class BookableAmenity:
        def __init__(self): self.name = "Mock BookableAmenity"


    print("Warning: Could not import Space models. Using dummy mocks. Admin functionality will be limited.")

import logging

logger = logging.getLogger(__name__)


# --- 明确移除所有 Mock 对象定义代码 ---
# (原文件中此部分已被删除或注释，此处不再包含以保持代码整洁)
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
    # 确保 reviewed_at 在 readonly_fields 中，因为它由系统填充
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
            return qs.none()  # 如果无法导入 Space 模型，那么基于其权限的过滤是无法进行的

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
        # 针对特定预订对象，检查用户是否对该预订所在的 Space 拥有 can_view_space_bookings 对象级权限
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
            actions.pop('delete_selected', None)  # 空间管理员不能批量删除

            filtered_actions = {}
            for action_name in space_manager_specific_actions:
                if action_name in actions:
                    filtered_actions[action_name] = actions[action_name]
            return filtered_actions
        else:
            if 'delete_selected' in actions: del actions[
                'delete_selected']  # 超级用户可以批量删除，但通常建议禁用管理员批量删除操作，使用更细粒度的控制，这里暂时移除
            return actions

    # --- Admin Actions 统一修改为 QuerySet.update() ---

    def _process_booking_action(self, request, queryset, new_status, action_type, status_conditions,
                                permission_codename=None, update_reviewer_info=False, check_end_time=False,
                                create_violation=False, violation_type=None):
        """
        一个通用的 helper 方法来处理所有 Booking 状态相关的 Admin Actions。
        """
        if not request.user.is_authenticated:
            self.message_user(request, "您没有权限执行此操作，请先登录。", messages.ERROR)
            return 0, [], []

        processed_count = 0
        error_messages = []
        warning_messages = []
        violation_count = 0  # 仅用于 mark_no_show_and_violate

        for booking in queryset:
            logger.debug(f"Admin Action '{action_type}': Processing booking ID {booking.id}, "
                         f"current status: {booking.status}, processing_status: {booking.processing_status}")

            target_space = booking.space or (booking.bookable_amenity.space if booking.bookable_amenity else None)
            if not target_space and SPACES_MODELS_LOADED:  # 如果 Space 模型未加载，则无法基于 target_space 进行检查
                error_messages.append(f"预订 {booking.id} 的目标空间无效，无法执行 '{action_type}'。")
                logger.error(f"Admin Action '{action_type}': Booking {booking.id} has no valid target space.")
                continue

            # 权限检查
            if not (request.user.is_superuser or getattr(request.user, 'is_system_admin', False) or \
                    (getattr(request.user, 'is_space_manager', False) and permission_codename and \
                     SPACES_MODELS_LOADED and request.user.has_perm(permission_codename, target_space))):
                perm_needed = permission_codename if permission_codename else "ALL_ADMIN_PERMS"
                error_messages.append(f"您没有权限对预订 {booking.id} 执行 '{action_type}' (需要 {perm_needed})。")
                logger.warning(
                    f"Admin Action '{action_type}': User {request.user.username} lacks permission '{perm_needed}' for booking {booking.id}.")
                continue

            # 状态检查
            if booking.status not in status_conditions:
                warning_messages.append(
                    f"预订 {booking.id} 状态为 {booking.status}，无法执行 '{action_type}'。当前状态不允许此操作。")
                logger.warning(
                    f"Admin Action '{action_type}': Booking {booking.id} status is {booking.status}, not in allowed conditions: {status_conditions}.")
                continue

            # 结束时间检查 (例如 '未到场' 动作)
            if check_end_time and booking.end_time >= timezone.now():
                warning_messages.append(f"预订 {booking.id} 尚未结束，无法标记为未到场。")
                logger.warning(
                    f"Admin Action '{action_type}': Booking {booking.id} end time is not in the past for 'NO_SHOW'.")
                continue

            # 执行更新
            try:
                if booking.pk is None:
                    error_messages.append(f"内部错误：选中的预订实例 {booking.id} 没有主键ID。")
                    logger.critical(
                        f"Admin Action '{action_type}': A selected booking instance ({booking.id}) has no PK. Highly unexpected.")
                    continue

                update_fields = {'status': new_status, 'updated_at': timezone.now()}
                if update_reviewer_info:
                    update_fields['reviewed_by'] = request.user
                    update_fields['reviewed_at'] = timezone.now()

                # 使用 QuerySet.update() 执行更新
                # 再次过滤确保状态满足条件，处理并发
                update_filter = Q(pk=booking.pk, status__in=status_conditions)
                if check_end_time:  # 只有当需要检查 end_time 时才加入这个过滤条件
                    update_filter &= Q(end_time__lt=timezone.now())

                updated_rows = queryset.filter(update_filter).update(**update_fields)

                if updated_rows > 0:
                    processed_count += 1
                    logger.info(
                        f"Admin Action '{action_type}': Successfully updated booking ID: {booking.id} to {new_status}.")

                    # 特殊处理：创建违规记录 (仅用于 mark_no_show_and_violate)
                    if create_violation and target_space:
                        space_type_for_violation = target_space.space_type if target_space else None
                        if space_type_for_violation:
                            Violation.objects.create(
                                user=booking.user, booking=booking, space_type=space_type_for_violation,
                                violation_type=violation_type,
                                description=f"用户 {booking.user.get_full_name} 在 {getattr(target_space, 'name', '未知空间')} 预订中未到场。",
                                issued_by=request.user, penalty_points=1
                            )
                            violation_count += 1
                            logger.info(
                                f"Admin Action '{action_type}': Created violation for booking ID: {booking.id}.")
                        else:
                            warning_messages.append(f"预订 {booking.id} 无法确定空间类型，未能创建违规记录。")
                            logger.warning(
                                f"Admin Action '{action_type}': Booking {booking.id} space type not found for violation.")

                else:
                    warning_messages.append(f"预订 {booking.id} 未被更新，可能其状态已不满足操作条件，或 ID 不匹配。")
                    logger.warning(
                        f"Admin Action '{action_type}': Booking {booking.id} (updated_rows=0) was not updated. Conditions not met.")

            except IntegrityError as e:
                error_messages.append(f"执行 '{action_type}' 失败 (预订 {booking.id})：数据库完整性错误（{e}）。")
                logger.critical(f"Admin Action '{action_type}': IntegrityError for booking ID {booking.id}: {e}",
                                exc_info=True)
            except Exception as e:
                error_messages.append(f"执行 '{action_type}' 失败 (预订 {booking.id})：未知错误（{e}）。")
                logger.exception(f"Admin Action '{action_type}': Unexpected error for booking ID {booking.id}.")

        return processed_count, error_messages, warning_messages, violation_count

    @admin.action(description="批准选择的预订")
    def approve_bookings(self, request, queryset):
        processed_count, error_messages, warning_messages, _ = self._process_booking_action(
            request, queryset,
            new_status=Booking.BOOKING_STATUS_APPROVED,
            action_type="批准",
            status_conditions=[Booking.BOOKING_STATUS_PENDING],
            permission_codename='spaces.can_approve_space_bookings',
            update_reviewer_info=True
        )
        for msg in error_messages: self.message_user(request, msg, messages.ERROR)
        for msg in warning_messages: self.message_user(request, msg, messages.WARNING)
        if not error_messages and not warning_messages:
            self.message_user(request, f"成功批准了 {processed_count} 条预订。", messages.SUCCESS)

    @admin.action(description="拒绝选择的预订")
    def reject_bookings(self, request, queryset):
        processed_count, error_messages, warning_messages, _ = self._process_booking_action(
            request, queryset,
            new_status=Booking.BOOKING_STATUS_REJECTED,
            action_type="拒绝",
            status_conditions=[Booking.BOOKING_STATUS_PENDING],
            permission_codename='spaces.can_approve_space_bookings',
            update_reviewer_info=True
        )
        for msg in error_messages: self.message_user(request, msg, messages.ERROR)
        for msg in warning_messages: self.message_user(request, msg, messages.WARNING)
        if not error_messages and not warning_messages:
            self.message_user(request, f"成功拒绝了 {processed_count} 条预订。", messages.SUCCESS)

    @admin.action(description="取消选择的预订")
    def cancel_bookings(self, request, queryset):
        processed_count, error_messages, warning_messages, _ = self._process_booking_action(
            request, queryset,
            new_status=Booking.BOOKING_STATUS_CANCELLED,
            action_type="取消",
            status_conditions=[Booking.BOOKING_STATUS_PENDING, Booking.BOOKING_STATUS_APPROVED,
                               Booking.BOOKING_STATUS_CHECKED_IN],
            permission_codename='spaces.can_cancel_space_bookings',
            update_reviewer_info=True  # 取消操作也记录审核者
        )
        for msg in error_messages: self.message_user(request, msg, messages.ERROR)
        for msg in warning_messages: self.message_user(request, msg, messages.WARNING)
        if not error_messages and not warning_messages:
            self.message_user(request, f"成功取消了 {processed_count} 条预订。", messages.SUCCESS)

    @admin.action(description="标记为已完成")
    def mark_completed_bookings(self, request, queryset):
        processed_count, error_messages, warning_messages, _ = self._process_booking_action(
            request, queryset,
            new_status=Booking.BOOKING_STATUS_COMPLETED,
            action_type="标记完成",
            status_conditions=[Booking.BOOKING_STATUS_CHECKED_IN],
            permission_codename='spaces.can_checkin_space_bookings',  # 通常签到和完成是同一个权限
            update_reviewer_info=False  # 完成不记录审核者
        )
        for msg in error_messages: self.message_user(request, msg, messages.ERROR)
        for msg in warning_messages: self.message_user(request, msg, messages.WARNING)
        if not error_messages and not warning_messages:
            self.message_user(request, f"成功标记 {processed_count} 条预订为已完成。", messages.SUCCESS)

    @admin.action(description="标记为已签到")
    def mark_checked_in(self, request, queryset):
        processed_count, error_messages, warning_messages, _ = self._process_booking_action(
            request, queryset,
            new_status=Booking.BOOKING_STATUS_CHECKED_IN,
            action_type="标记签到",
            status_conditions=[Booking.BOOKING_STATUS_PENDING, Booking.BOOKING_STATUS_APPROVED],
            permission_codename='spaces.can_checkin_space_bookings',
            update_reviewer_info=False  # 签到不记录审核者
        )
        for msg in error_messages: self.message_user(request, msg, messages.ERROR)
        for msg in warning_messages: self.message_user(request, msg, messages.WARNING)
        if not error_messages and not warning_messages:
            self.message_user(request, f"成功标记 {processed_count} 条预订为已签到。", messages.SUCCESS)

    @admin.action(description="标记为未到场并创建违规记录")
    def mark_no_show_and_violate(self, request, queryset):
        processed_count, error_messages, warning_messages, violation_count = self._process_booking_action(
            request, queryset,
            new_status=Booking.BOOKING_STATUS_NO_SHOW,
            action_type="标记未到场",
            status_conditions=[Booking.BOOKING_STATUS_PENDING, Booking.BOOKING_STATUS_APPROVED],
            permission_codename='spaces.can_mark_no_show_and_create_violation',
            update_reviewer_info=False,
            check_end_time=True,  # 需要检查结束时间已过
            create_violation=True,
            violation_type=Violation.VIOLATION_TYPE_NO_SHOW  # 从 Violation 模型获取常量
        )
        for msg in error_messages: self.message_user(request, msg, messages.ERROR)
        for msg in warning_messages: self.message_user(request, msg, messages.WARNING)
        if not error_messages and not warning_messages:
            self.message_user(request, f"成功标记 {processed_count} 条预订为未到场，创建 {violation_count} 条违规记录。",
                              messages.SUCCESS)