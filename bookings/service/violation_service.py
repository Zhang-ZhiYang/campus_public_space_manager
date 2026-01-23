# bookings/service/violation_service.py
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction, models
from django.utils import timezone
from typing import List, Tuple, Optional, Dict, Any, Set
from django.db.models import QuerySet, Q
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

from bookings.models import Violation, UserPenaltyPointsPerSpaceType, SpaceTypeBanPolicy, UserSpaceTypeBan, \
    Booking  # import Booking

from spaces.models import Space, SpaceType
from users.models import CustomUser

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, InternalServerError
from guardian.shortcuts import get_objects_for_user, assign_perm

# ====================================================================
# 模块级别的辅助函数 (从 bookings.signals 导入，防止循环依赖)
# NOTE: _get_violation_target_space_type is NO LONGER imported directly FROM signals here.
# Instead, the logic to infer space_type from a booking is directly implemented within
# _create_no_show_violation_for_booking to adhere to the Open/Closed Principle.
from bookings.signals import (
    # _get_violation_target_space_type, # This import is removed.
    _recalculate_user_penalty_points,
    _apply_ban_policy
)


class ViolationService(BaseService):
    _dao_map = {
        'violation_dao': 'violation',
        'booking_dao': 'booking',
        'penalty_dao': 'user_penalty_points',  # Add penalty DAO
    }

    def __init__(self):
        super().__init__()
        self.violation_dao = self._get_dao_instance('violation')
        self.booking_dao = self._get_dao_instance('booking')
        self.penalty_dao = self._get_dao_instance('user_penalty_points')  # Init penalty DAO

    def get_admin_violations_queryset(self, user: CustomUser) -> ServiceResult[QuerySet[Violation]]:
        if user.is_superuser or user.is_system_admin:
            return ServiceResult.success_result(
                data=self.violation_dao.get_queryset(),
                message="成功获取所有违规记录。"
            )

        # Ensure user is CustomUser instance
        ActualCustomUser = get_user_model()
        if not isinstance(user, ActualCustomUser):
            # Attempt to fetch the actual user instance, although usually request.user is already a CustomUser
            try:
                user = ActualCustomUser.objects.get(pk=user.pk)
            except ActualCustomUser.DoesNotExist:
                return ServiceResult.error_result(
                    message="用户实例未找到。", error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

        if user.is_authenticated and user.has_perm('bookings.can_view_all_violations'):
            return ServiceResult.success_result(
                data=self.violation_dao.get_queryset(),
                message="成功获取所有违规记录。"
            )

        if user.is_authenticated and user.is_space_manager:
            # Replaced ContentType import with direct model references for simplicity if not strictly needed elsewhere
            # space_ct = ContentType.objects.get_for_model(Space)
            managed_spaces = get_objects_for_user(
                user, 'spaces.can_view_space', klass=Space
            )
            managed_spacetype_ids = []
            for space in managed_spaces:
                if space.space_type:
                    managed_spacetype_ids.append(space.space_type.id)

            explicitly_viewable_violations = get_objects_for_user(
                user, 'bookings.can_view_violation_record', klass=Violation
            )

            queryset = self.violation_dao.get_queryset().filter(
                Q(space_type__id__in=managed_spacetype_ids) |
                Q(booking__space__space_type__id__in=managed_spacetype_ids) |
                Q(booking__bookable_amenity__space__space_type__id__in=managed_spacetype_ids) |
                Q(pk__in=explicitly_viewable_violations)
            ).distinct()
            return ServiceResult.success_result(
                data=queryset,
                message="成功获取管理的违规记录。"
            )

        return ServiceResult.error_result(
            message="您没有权限查看违规记录。",
            error_code=ForbiddenException.default_code,
            status_code=ForbiddenException.status_code
        )

    @transaction.atomic
    def save_violation(self, user: CustomUser, violation_data: Dict[str, Any]) -> ServiceResult[Violation]:
        violation_id = violation_data.get('id')
        violation_obj = None

        booking_instance_from_data = violation_data.get('booking')
        if not violation_data.get('space_type') and booking_instance_from_data:
            # If booking_instance_from_data is a Booking instance, access its attributes
            if isinstance(booking_instance_from_data, Booking):  # Ensure it's a Booking instance
                if booking_instance_from_data.space and booking_instance_from_data.space.space_type:
                    violation_data['space_type'] = booking_instance_from_data.space.space_type
                elif booking_instance_from_data.bookable_amenity and booking_instance_from_data.bookable_amenity.space \
                        and booking_instance_from_data.bookable_amenity.space.space_type:
                    violation_data['space_type'] = booking_instance_from_data.bookable_amenity.space.space_type

        # Ensure user is CustomUser instance, or handle the case where it might be a raw ID
        ActualCustomUser = get_user_model()
        if 'user' in violation_data and not isinstance(violation_data['user'], ActualCustomUser):
            try:
                violation_data['user'] = ActualCustomUser.objects.get(pk=violation_data['user'])
            except ActualCustomUser.DoesNotExist:
                return ServiceResult.error_result(message="指定的用户不存在。",
                                                  error_code=NotFoundException.default_code,
                                                  status_code=NotFoundException.status_code)

        if violation_id:
            violation_obj = self.violation_dao.get_violation_by_id(violation_id)
            if not violation_obj:
                return ServiceResult.error_result(
                    message="违规记录未找到。", error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # Permissions check for editing
            is_system_admin = user.is_superuser or getattr(user, 'is_system_admin', False)
            if not is_system_admin:
                if user.has_perm('bookings.can_edit_violation_record'):
                    pass  # User has global edit permission
                elif violation_obj.space_type and user.is_space_manager:
                    managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)
                    if not managed_spacetypes.filter(id=violation_obj.space_type.id).exists():
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无编辑此空间类型违规记录权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                else:
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (无编辑违规记录权限)",
                        error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                    )
        else:  # Creating a new violation
            is_system_admin = user.is_superuser or getattr(user, 'is_system_admin', False)
            if not is_system_admin:
                if not user.has_perm('bookings.can_create_violation_record'):
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (无创建违规记录权限)",
                        error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                    )
                if user.is_space_manager and 'space_type' in violation_data and violation_data['space_type']:
                    managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)
                    if not managed_spacetypes.filter(id=violation_data['space_type'].id).exists():
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无创建此空间类型违规记录权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                elif user.is_space_manager and (
                        'space_type' not in violation_data or violation_data['space_type'] is None):
                    # Space manager is trying to create a global violation without system admin permission
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (空间管理员无法创建全局违规记录)",
                        error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                    )

        try:
            if violation_id:
                # _old_* attributes are no longer set here, they are handled by pre_save signal now

                # Check for resolution changes if not a system admin
                if not is_system_admin:
                    # Non-system admins can only change 'is_resolved', 'resolved_by', 'resolved_at'
                    allowed_fields_for_resolution = {'is_resolved', 'resolved_by', 'resolved_at'}
                    if any(field not in allowed_fields_for_resolution for field in violation_data.keys()):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (您没有权限修改此违规记录的非解决状态字段)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )

                updated_violation = self.violation_dao.update_violation(violation_obj, **violation_data)
                return ServiceResult.success_result(data=updated_violation, message="违规记录更新成功。")
            else:
                new_violation = self.violation_dao.create_violation(user=violation_data['user'], **violation_data)
                # Assign object-level permissions if created by a space manager for their managed space type
                from guardian.shortcuts import assign_perm  # Import here to avoid potential circular dependency
                if user.is_space_manager and new_violation.space_type:
                    assign_perm('bookings.can_edit_violation_record', user, new_violation)
                    assign_perm('bookings.can_resolve_violation_record', user, new_violation)
                return ServiceResult.success_result(data=new_violation, message="违规记录创建成功。", status_code=201)
        except Exception as e:
            logger.error(f"Error saving violation {violation_id if violation_id else 'new'}: {e}", exc_info=True)
            return self._handle_exception(e, default_message=f"保存违规记录失败: {e}")

    @transaction.atomic
    def mark_violations_resolved(self, user: CustomUser, pk_list: List[int]) -> ServiceResult[Tuple[int, int]]:
        resolved_count = 0
        warnings = []
        errors = []

        # 获取实际的 CustomUser 模型类
        ActualCustomUser = get_user_model()
        if not isinstance(user, ActualCustomUser):
            try:
                user = ActualCustomUser.objects.get(pk=user.pk)
            except ActualCustomUser.DoesNotExist:
                return ServiceResult.error_result(
                    message="当前操作用户不存在。", error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )
        is_system_admin_or_superuser = user.is_superuser or getattr(user, 'is_system_admin', False)

        has_global_resolve_perm = is_system_admin_or_superuser or user.has_perm('bookings.can_resolve_violation_record')

        queryset = self.violation_dao.filter(pk__in=pk_list).select_related('space_type',
                                                                            'booking__space__space_type')  # 预加载相关字段

        for violation in queryset:
            target_space = violation.booking.related_space if violation.booking else None  # 优先从 booking 的 related_space 获取

            # --- 权限检查增强 ---
            can_resolve_single_violation = False
            if is_system_admin_or_superuser:
                can_resolve_single_violation = True
            elif user.is_space_manager and user.has_perm('bookings.can_resolve_violation_record', violation):
                # Space manager has object-level permission for this specific violation
                can_resolve_single_violation = True
            elif user.is_space_manager and target_space:
                # Fallback: if not obj level perm, check if they manage the space associated with the violation
                can_resolve_single_violation = user.has_perm('spaces.can_view_space_bookings', target_space)
            elif user.is_space_manager and violation.space_type:
                # If no specific target_space, check if they manage the space_type
                # This needs careful implementation for object-level permission using SpaceType
                # For simplicity, if space_type is present as managed, allow. This needs `get_managed_spacetypes_by_user`.
                managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)
                if managed_spacetypes.filter(id=violation.space_type.id).exists():
                    can_resolve_single_violation = True

            if not can_resolve_single_violation:
                errors.append(f"您没有权限解决违规 {violation.id}。")
                logger.warning(
                    f"User {user.id} attempted to resolve violation {violation.id} without sufficient permission.")
                continue
            # --- 权限检查增强结束 ---

            try:
                if not violation.is_resolved:
                    # 使用 DAO 的新方法来更新状态，它会确保 save() 被调用
                    updated_violation = self.violation_dao.update_violation_status(
                        violation_id=violation.pk,
                        is_resolved=True,
                        resolved_by=user
                    )
                    if updated_violation:
                        resolved_count += 1
                        logger.info(f"Violation {violation.id} marked as resolved by user {user.id}.")
                    else:
                        errors.append(f"解决违规 {violation.id} 失败：更新操作未生效。")
                else:
                    warnings.append(f"违规 {violation.id} 已是解决状态，无需重复操作。")
            except Exception as e:
                errors.append(f"解决违规 {violation.id} 失败: {e}")
                logger.error(f"Error resolving violation {violation.id}: {e}", exc_info=True)

        if errors:
            return ServiceResult.error_result(
                message="部分违规记录解决失败", errors=errors + warnings,
                error_code=BadRequestException.default_code, status_code=BadRequestException.status_code
            )
        return ServiceResult.success_result(
            data=(resolved_count, len(warnings)),
            message=f"成功解决了 {resolved_count} 条违约记录。", warnings=warnings
        )

    @transaction.atomic
    def _create_no_show_violation_for_booking(self, booking: Booking, issued_by_user: Optional[CustomUser] = None) -> \
    ServiceResult[Violation]:
        """
        内部方法：为指定的预订创建一条“未到场”违规记录。
        此方法旨在供自动化任务或已通过权限检查的流程调用，不执行额外的权限检查。
        它会同时将预订状态更新为 `NO_SHOW`。

        :param booking: 关联的 Booking 实例。此实例应已预加载 'user', 'space', 'bookable_amenity', 'related_space' 等相关字段。
        :param issued_by_user: 记录此违规的用户 (如果是手动操作)。对于自动化任务，可为 None。
        :return: ServiceResult，包含新创建的 Violation 实例。
        """
        if booking.status not in [Booking.BOOKING_STATUS_PENDING, Booking.BOOKING_STATUS_APPROVED]:
            logger.debug(
                f"Booking {booking.pk} status is {booking.status}, not PENDING or APPROVED. Cannot mark as NO_SHOW.")
            return ServiceResult.error_result(
                message=f"预订 {booking.pk} 状态为 {booking.get_status_display()}，无法标记为未到场。",
                error_code="invalid_booking_status_for_no_show",
                status_code=400
            )

        # 确保预订已过期
        if booking.end_time >= timezone.now():
            logger.debug(
                f"Booking {booking.pk} end_time ({booking.end_time}) has not passed yet. Cannot mark as NO_SHOW.")
            return ServiceResult.error_result(
                message=f"预订 {booking.pk} 尚未过期，无法标记为未到场。",
                error_code="booking_not_overdue_for_no_show",
                status_code=400
            )

        # 标记预订为 NO_SHOW
        # Use a dict for update fields for clarity and potential future expansion
        update_fields = {'status': Booking.BOOKING_STATUS_NO_SHOW}
        admin_notes_prefix = f"系统自动标记为未到场，因预订过期未签到。"
        if booking.admin_notes:
            update_fields['admin_notes'] = f"{booking.admin_notes}\n{admin_notes_prefix}" + (
                f" 操作员: {issued_by_user.username}" if issued_by_user else "")
        else:
            update_fields['admin_notes'] = admin_notes_prefix + (
                f" 操作员: {issued_by_user.username}" if issued_by_user else "")

        updated_booking = self.booking_dao.update(
            booking,  # 直接传入 booking 实例
            **update_fields
        )
        if not updated_booking:
            raise InternalServerError(f"更新预订 {booking.pk} 状态为 NO_SHOW 失败。")

        # --- IMPORTANT CHANGE: Infer space_type directly from Booking (without signals helper) ---
        space_type_for_violation = None
        if updated_booking.space and updated_booking.space.space_type:
            space_type_for_violation = updated_booking.space.space_type
        elif updated_booking.bookable_amenity and \
                updated_booking.bookable_amenity.space and \
                updated_booking.bookable_amenity.space.space_type:
            space_type_for_violation = updated_booking.bookable_amenity.space.space_type
        elif updated_booking.related_space and updated_booking.related_space.space_type:  # Fallback to related_space
            space_type_for_violation = updated_booking.related_space.space_type
        # --- END IMPORTANT CHANGE ---

        if not space_type_for_violation:
            logger.warning(
                f"Could not determine space type for booking {updated_booking.pk}. Violation may not apply correctly for penalty points.")

        # 创建违规记录
        try:
            target_space_name = getattr(booking.related_space, 'name', '未知空间') if booking.related_space else '未知空间'
            violation_data_for_creation = {
                'user': booking.user,
                'booking': booking,  # 关联原始 booking
                'space_type': space_type_for_violation,
                'violation_type': Violation.VIOLATION_TYPE_NO_SHOW,
                'description': f"用户 {booking.user.get_full_name} 未在 {target_space_name} (预订ID: {booking.pk}) 预订中签到，系统自动创建。",
                'issued_by': issued_by_user,  # 对于自动化任务，此项为 None
                'penalty_points': 1  # 未到场的默认违约点数
            }
            new_violation = self.violation_dao.create(**violation_data_for_creation)  # 使用 DAO 的 create 方法
            logger.info(f"Violation ID:{new_violation.pk} created for booking {booking.pk} (user {booking.user.pk}).")
            return ServiceResult.success_result(data=new_violation, message="未到场违规记录创建成功。")
        except Exception as e:
            logger.exception(f"Failed to create violation for booking {booking.pk}: {e}")
            raise InternalServerError(f"创建未到场违规记录失败: {e}")

    @transaction.atomic
    def mark_no_show_and_violate(self, user: CustomUser, pk_list: List[int]) -> ServiceResult[Tuple[int, int]]:
        """
        批量标记预订为未到场并创建违规记录。
        此方法用于API层的手动操作，会执行权限检查。
        """
        no_show_count = 0
        violation_count = 0
        warnings = []
        errors = []

        ActualCustomUser = get_user_model()
        if not isinstance(user, ActualCustomUser):
            try:
                user = ActualCustomUser.objects.get(pk=user.pk)
            except ActualCustomUser.DoesNotExist:
                return ServiceResult.error_result(
                    message="当前操作用户不存在。", error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )
        is_system_admin_or_superuser = user.is_superuser or getattr(user, 'is_system_admin', False)

        has_global_mark_no_show_and_violate_perm = is_system_admin_or_superuser or \
                                                   user.has_perm('bookings.can_mark_no_show_and_create_violation')
        has_global_create_violation_perm = is_system_admin_or_superuser or \
                                           user.has_perm('bookings.can_create_violation_record')

        # 预加载 bookings，减少循环内的数据库查询
        bookings_to_process = self.booking_dao.filter(pk__in=pk_list).select_related(
            'user', 'space__space_type', 'bookable_amenity__space__space_type', 'related_space__space_type'
        )

        for booking in bookings_to_process:
            target_space = booking.related_space  # 已预加载

            can_mark_single_no_show = has_global_mark_no_show_and_violate_perm or \
                                      (target_space and user.has_perm('spaces.can_checkin_space_bookings',
                                                                      target_space))

            # 创建违规记录的权限检查
            can_create_single_violation_record = has_global_create_violation_perm
            if not can_create_single_violation_record and user.is_space_manager and target_space and target_space.space_type:
                managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)
                if managed_spacetypes.filter(id=target_space.space_type.id).exists():
                    can_create_single_violation_record = True

            if not can_mark_single_no_show:
                errors.append(f"您没有权限对预订 {booking.id} 进行未到场标记。")
                logger.warning(f"User {user.id} has no permission to mark no-show for booking {booking.id}.")
                continue

            # 仅处理状态为 APPROVED 或 PENDING 且已过期的预订
            # 内部方法 _create_no_show_violation_for_booking 会进行状态和过期时间检查，因此这里只需简单判断即可。
            if booking.status in [Booking.BOOKING_STATUS_PENDING, Booking.BOOKING_STATUS_APPROVED] \
                    and booking.end_time < timezone.now():
                try:
                    # 调用内部方法来处理每个预订，并由当前用户作为操作者
                    service_result = self._create_no_show_violation_for_booking(booking=booking, issued_by_user=user)

                    if service_result.success:
                        no_show_count += 1
                        violation_count += 1  # 如果内部方法成功，则违规记录也已创建
                    else:
                        errors.append(f"标记预订 {booking.id} 为未到场或创建违规记录失败: {service_result.message}")
                except Exception as e:
                    errors.append(f"标记预订 {booking.id} 为未到场或创建违规记录失败: {e}")
                    logger.error(f"Error marking booking {booking.id} as no-show or creating violation: {e}",
                                 exc_info=True)
            else:
                warnings.append(f"预订 {booking.id} 状态为 {booking.get_status_display()} 或未过期，无法标记为未到场。")

        if errors:
            return ServiceResult.error_result(
                message="部分操作失败", errors=errors + warnings,
                error_code=BadRequestException.default_code, status_code=BadRequestException.status_code
            )
        return ServiceResult.success_result(
            data=(no_show_count, violation_count),
            message=f"成功标记 {no_show_count} 条预订为未到场，创建 {violation_count} 条违规记录。",
            warnings=warnings
        )

    @transaction.atomic
    def recalculate_and_apply_ban_policies_for_user_and_space_type(
            self, user: CustomUser, space_type: Optional[SpaceType]
    ) -> ServiceResult[UserPenaltyPointsPerSpaceType]:
        """
        重新计算用户在给定空间类型（或全局）下的活跃违约点数，
        并根据当前的违约点数评估和应用禁用策略。
        此方法主要供定时任务调用。
        """
        try:
            # 1. 重新计算活跃点数
            current_total_active_points = _recalculate_user_penalty_points(user, space_type)

            # 2. 获取或创建 UserPenaltyPointsPerSpaceType 记录
            penalty_points_record, created = UserPenaltyPointsPerSpaceType.objects.get_or_create(
                user=user,
                space_type=space_type,
                defaults={'current_penalty_points': current_total_active_points, 'last_violation_at': timezone.now()}
            )

            # --- 关键修改：无论点数是否变化，都确保更新最后违规时间并触发保存 ----
            # 即使 current_penalty_points 没有变化，但如果最后活跃时间有变更，也应该触发 save
            # 确保 updated_at 更新，并重新触发 post_save 信号调用 _apply_ban_policy
            needs_save = False
            if penalty_points_record.current_penalty_points != current_total_active_points:
                penalty_points_record.current_penalty_points = current_total_active_points
                penalty_points_record.last_violation_at = timezone.now()
                needs_save = True
                logger.info(
                    f"[Recalculate Task] User {user.pk} penalty points updated to {current_total_active_points} for space type {space_type.pk if space_type else 'Global'} (created: {created}).")
            elif created:  # 如果是新创建的记录
                penalty_points_record.last_violation_at = timezone.now()
                needs_save = True
                logger.info(
                    f"[Recalculate Task] New UserPenaltyPointsPerSpaceType record created for user {user.pk} in space type {space_type.pk if space_type else 'Global'} with {current_total_active_points} points.")

            if needs_save:
                # 只更新需要修改的字段，减少不必要的数据库写入。
                # 即使不需要更新 current_penalty_points，也更新 updated_at 确保 post_save 信号被触发。
                penalty_points_record.save(update_fields=['current_penalty_points', 'last_violation_at', 'updated_at'])
            else:
                # 即使没有数据字段变化，也要确保 _apply_ban_policy 被调用，
                # 因为禁用策略本身可能发生变化，或某个禁令已过期
                logger.debug(
                    f"[Recalculate Task] No penalty points update for user {user.pk} in space type {space_type.pk if space_type else 'Global'}. Re-applying ban policy for consistency.")
                _apply_ban_policy(penalty_points_record)  # 直接调用，因为 post_save 信号不会触发

            return ServiceResult.success_result(
                data=penalty_points_record,
                message=f"用户 {user.pk} 在 {space_type.name if space_type else '全局'} 的违约点数已更新并评估禁用策略。"
            )

        except Exception as e:
            logger.exception(
                f"Error in recalculate_and_apply_ban_policies_for_user_and_space_type for user {user.pk}, space type {space_type.pk if space_type else 'Global'}: {e}")
            return self._handle_exception(e, default_message="批处理违约点数和禁用策略失败。")