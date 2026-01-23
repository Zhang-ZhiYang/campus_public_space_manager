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

from bookings.models import Violation, UserPenaltyPointsPerSpaceType, SpaceTypeBanPolicy, UserSpaceTypeBan, Booking
from spaces.models import Space, SpaceType  # 确保导入 Space 和 SpaceType
from spaces.models import (  # <--- 从 spaces.models 导入常量
    CHECK_IN_METHOD_NONE,
    CHECK_IN_METHOD_SELF,  # 虽然这里不直接使用，但为了导入 CHECK_IN_METHOD_HYBRID，最好把相关常量都导入
    CHECK_IN_METHOD_STAFF,
    CHECK_IN_METHOD_HYBRID
)
from users.models import CustomUser

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, InternalServerError
from guardian.shortcuts import get_objects_for_user, assign_perm

from bookings.signals import (
    _recalculate_user_penalty_points,
    _apply_ban_policy
)


class ViolationService(BaseService):
    _dao_map = {
        'violation_dao': 'violation',
        'booking_dao': 'booking',
        'penalty_dao': 'user_penalty_points',
    }

    def __init__(self):
        super().__init__()
        self.violation_dao = self._get_dao_instance('violation')
        self.booking_dao = self._get_dao_instance('booking')
        self.penalty_dao = self._get_dao_instance('user_penalty_points')

    def get_admin_violations_queryset(self, user: CustomUser) -> ServiceResult[QuerySet[Violation]]:
        # ... (此方法保持不变)
        if user.is_superuser or user.is_system_admin:
            return ServiceResult.success_result(
                data=self.violation_dao.get_queryset(),
                message="成功获取所有违规记录。"
            )

        ActualCustomUser = get_user_model()
        if not isinstance(user, ActualCustomUser):
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

    def save_violation(self, user: CustomUser, violation_data: Dict[str, Any]) -> ServiceResult[Violation]:
        # ... (此方法保持不变)
        violation_id = violation_data.get('id')
        violation_obj = None
        booking_instance_from_data = violation_data.get('booking')
        if not violation_data.get('space_type') and booking_instance_from_data:
            if isinstance(booking_instance_from_data, Booking):
                if booking_instance_from_data.space and booking_instance_from_data.space.space_type:
                    violation_data['space_type'] = booking_instance_from_data.space.space_type
                elif booking_instance_from_data.bookable_amenity and booking_instance_from_data.bookable_amenity.space \
                        and booking_instance_from_data.bookable_amenity.space.space_type:
                    violation_data['space_type'] = booking_instance_from_data.bookable_amenity.space.space_type
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
            is_system_admin = user.is_superuser or getattr(user, 'is_system_admin', False)
            if not is_system_admin:
                if user.has_perm('bookings.can_edit_violation_record'):
                    pass
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
        else:
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
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (空间管理员无法创建全局违规记录)",
                        error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                    )
        try:
            if violation_id:
                if not is_system_admin:
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
                from guardian.shortcuts import assign_perm
                if user.is_space_manager and new_violation.space_type:
                    assign_perm('bookings.can_edit_violation_record', user, new_violation)
                    assign_perm('bookings.can_resolve_violation_record', user, new_violation)
                return ServiceResult.success_result(data=new_violation, message="违规记录创建成功。", status_code=201)
        except Exception as e:
            logger.error(f"Error saving violation {violation_id if violation_id else 'new'}: {e}", exc_info=True)
            return self._handle_exception(e, default_message=f"保存违规记录失败: {e}")

    @transaction.atomic
    def mark_violations_resolved(self, user: CustomUser, pk_list: List[int]) -> ServiceResult[Tuple[int, int]]:
        # ... (此方法保持不变)
        resolved_count = 0
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
        has_global_resolve_perm = is_system_admin_or_superuser or user.has_perm('bookings.can_resolve_violation_record')
        queryset = self.violation_dao.filter(pk__in=pk_list).select_related('space_type',
                                                                            'booking__space__space_type')
        for violation in queryset:
            target_space = violation.booking.related_space if violation.booking else None
            can_resolve_single_violation = False
            if is_system_admin_or_superuser:
                can_resolve_single_violation = True
            elif user.is_space_manager and user.has_perm('bookings.can_resolve_violation_record', violation):
                can_resolve_single_violation = True
            elif user.is_space_manager and target_space:
                can_resolve_single_violation = user.has_perm('spaces.can_view_space_bookings', target_space)
            elif user.is_space_manager and violation.space_type:
                managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)
                if managed_spacetypes.filter(id=violation.space_type.id).exists():
                    can_resolve_single_violation = True
            if not can_resolve_single_violation:
                errors.append(f"您没有权限解决违规 {violation.id}。")
                logger.warning(
                    f"User {user.id} attempted to resolve violation {violation.id} without sufficient permission.")
                continue
            try:
                if not violation.is_resolved:
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
            ServiceResult[Optional[Violation]]:  # <--- 返回类型可以是 None
        """
        内部方法：为指定的预订创建一条“未到场”违规记录。
        此方法旨在供自动化任务或已通过权限检查的流程调用，不执行额外的权限检查。
        它会同时将预订状态更新为 `NO_SHOW` 或 `COMPLETED`（如果无需签到）。

        :param booking: 关联的 Booking 实例。此实例应已预加载 'user', 'space', 'bookable_amenity', 'related_space' 等相关字段。
        :param issued_by_user: 记录此违规的用户 (如果是手动操作)。对于自动化任务，可为 None。
        :return: ServiceResult，包含新创建的 Violation 实例，或在无需签到时返回 None。
        """
        # 确保预订已过期 (在最开始进行，因为无论签到方式如何，过期都是前提)
        if booking.end_time >= timezone.now():
            logger.debug(
                f"Booking {booking.pk} end_time ({booking.end_time}) has not passed yet. Cannot process NO_SHOW / COMPLETED.")
            return ServiceResult.error_result(
                message=f"预订 {booking.pk} 尚未过期，无法处理为未到场或完成状态。",
                error_code="booking_not_overdue_for_no_show",
                status_code=400
            )

        # 获取关联空间 (已假设预加载)
        related_space = booking.related_space
        if not related_space:
            logger.error(f"Booking {booking.pk} 无法找到关联空间，无法判断签到方式。")
            return ServiceResult.error_result(
                message=f"预订 {booking.pk} 无法找到关联空间，无法判断签到方式。",
                error_code="no_related_space_for_noshow", status_code=InternalServerError.status_code
            )

        # 获取有效的签到方式，逻辑与 Space.to_dict 和 Space.save 保持一致
        effective_check_in_method = related_space.check_in_method
        if effective_check_in_method is None or effective_check_in_method == '':
            if related_space.space_type:
                effective_check_in_method = related_space.space_type.default_check_in_method
            else:
                effective_check_in_method = CHECK_IN_METHOD_HYBRID  # 最终的兜底，如果 Space 和 SpaceType 都没有设置

        # --- 针对 不需要签到 的逻辑分支 ---
        if effective_check_in_method == CHECK_IN_METHOD_NONE:
            logger.info(f"预订 {booking.pk} 关联空间({related_space.name})签到方式为 '不需要签到'。")
            if booking.status != Booking.BOOKING_STATUS_COMPLETED:
                update_fields = {'status': Booking.BOOKING_STATUS_COMPLETED}
                admin_notes_entry = f"\n[{timezone.now().strftime('%Y-%m-%d %H:%M')}] 系统自动标记为 [已完成]。原因：关联空间({related_space.name})不需要签到，且预订已过期。"
                update_fields['admin_notes'] = (booking.admin_notes or '') + admin_notes_entry

                updated_booking = self.booking_dao.update(booking, **update_fields)
                if not updated_booking:
                    logger.error(f"更新预订 {booking.pk} 状态为 COMPLETED 失败。")
                    raise InternalServerError(f"更新预订 {booking.pk} 状态为 COMPLETED 失败。")
                logger.info(f"预订 {booking.pk} 已自动更新状态为 [已完成]，因为不需要签到。")
            else:
                logger.info(f"预订 {booking.pk} 状态已是 [已完成]，无需再次处理。")

            return ServiceResult.success_result(
                data=None,  # 此处不返回 Violation 实例
                message=f"预订 {booking.pk} 已自动完成，无需签到且不创建未到场违规。",
                status_code=200
            )
        # --- 针对需要签到但未签到的逻辑分支 (即原有的 NO_SHOW 逻辑) ---
        else:
            if booking.status != Booking.BOOKING_STATUS_APPROVED:  # 仅处理已批准的预订
                logger.debug(f"Booking {booking.pk} status is {booking.status}, not APPROVED. Cannot mark as NO_SHOW.")
                return ServiceResult.error_result(
                    message=f"预订 {booking.pk} 状态为 {booking.get_status_display()}，无法标记为未到场。",
                    error_code="invalid_booking_status_for_no_show",
                    status_code=400
                )

            # 标记预订为 NO_SHOW
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

            space_type_for_violation = None
            if updated_booking.space and updated_booking.space.space_type:
                space_type_for_violation = updated_booking.space.space_type
            elif updated_booking.bookable_amenity and \
                    updated_booking.bookable_amenity.space and \
                    updated_booking.bookable_amenity.space.space_type:
                space_type_for_violation = updated_booking.bookable_amenity.space.space_type
            elif updated_booking.related_space and updated_booking.related_space.space_type:
                space_type_for_violation = updated_booking.related_space.space_type

            if not space_type_for_violation:
                logger.warning(
                    f"Could not determine space type for booking {updated_booking.pk}. Violation may not apply correctly for penalty points.")

            # 创建违规记录
            try:
                target_space_name = getattr(booking.related_space, 'name',
                                            '未知空间') if booking.related_space else '未知空间'
                violation_data_for_creation = {
                    'user': booking.user,
                    'booking': booking,
                    'space_type': space_type_for_violation,
                    'violation_type': Violation.VIOLATION_TYPE_NO_SHOW,
                    'description': f"用户 {booking.user.get_full_name} 未在 {target_space_name} (预订ID: {booking.pk}) 预订中签到，系统自动创建。",
                    'issued_by': issued_by_user,
                    'penalty_points': 1
                }
                new_violation = self.violation_dao.create(**violation_data_for_creation)
                logger.info(
                    f"Violation ID:{new_violation.pk} created for booking {booking.pk} (user {booking.user.pk}).")

                # 处理用户违约点数和禁用策略
                _recalculate_user_penalty_points(updated_booking.user, space_type_for_violation)
                # 重新获取最新的记录，以便 _apply_ban_policy
                latest_penalty_record = UserPenaltyPointsPerSpaceType.objects.get(
                    user=updated_booking.user, space_type=space_type_for_violation
                )
                _apply_ban_policy(latest_penalty_record)

                return ServiceResult.success_result(data=new_violation, message="未到场违规记录创建成功。")
            except Exception as e:
                logger.exception(f"Failed to create violation for booking {booking.pk}: {e}")
                raise InternalServerError(f"创建未到场违规记录失败: {e}")

    @transaction.atomic
    def mark_no_show_and_violate(self, user: CustomUser, pk_list: List[int]) -> ServiceResult[Tuple[int, int]]:
        # ... (此方法保持不变)
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
        bookings_to_process = self.booking_dao.filter(pk__in=pk_list).select_related(
            'user', 'space__space_type', 'bookable_amenity__space__space_type', 'related_space__space_type'
        )
        for booking in bookings_to_process:
            target_space = booking.related_space
            can_mark_single_no_show = has_global_mark_no_show_and_violate_perm or \
                                      (target_space and user.has_perm('spaces.can_checkin_space_bookings',
                                                                      target_space))
            can_create_single_violation_record = has_global_create_violation_perm
            if not can_create_single_violation_record and user.is_space_manager and target_space and target_space.space_type:
                managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)
                if managed_spacetypes.filter(id=target_space.space_type.id).exists():
                    can_create_single_violation_record = True
            if not can_mark_single_no_show:
                errors.append(f"您没有权限对预订 {booking.id} 进行未到场标记。")
                logger.warning(f"User {user.id} has no permission to mark no-show for booking {booking.id}.")
                continue

            # 内部方法 _create_no_show_violation_for_booking 会进行状态、过期时间以及签到方式检查
            try:
                service_result = self._create_no_show_violation_for_booking(booking=booking, issued_by_user=user)
                if service_result.success:
                    no_show_count += 1
                    # 只有当确实创建了 Violation 时才计算入内
                    if service_result.data is not None:
                        violation_count += 1
                else:
                    errors.append(f"标记预订 {booking.id} 为未到场或创建违规记录失败: {service_result.message}")
            except Exception as e:
                errors.append(f"标记预订 {booking.id} 为未到场或创建违规记录失败: {e}")
                logger.error(f"Error marking booking {booking.id} as no-show or creating violation: {e}",
                             exc_info=True)
            else:
                warnings.append(
                    f"预订 {booking.id} 状态为 {booking.get_status_display()} 或未过期，无法标记为未到场。")  # 这一行可能会被新的逻辑覆盖，需要斟酌

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

    def recalculate_and_apply_ban_policies_for_user_and_space_type(
            self, user: CustomUser, space_type: Optional[SpaceType]
    ) -> ServiceResult[UserPenaltyPointsPerSpaceType]:
        # ... (此方法保持不变)
        try:
            current_total_active_points = _recalculate_user_penalty_points(user, space_type)
            penalty_points_record, created = UserPenaltyPointsPerSpaceType.objects.get_or_create(
                user=user,
                space_type=space_type,
                defaults={'current_penalty_points': current_total_active_points, 'last_violation_at': timezone.now()}
            )
            needs_save = False
            if penalty_points_record.current_penalty_points != current_total_active_points:
                penalty_points_record.current_penalty_points = current_total_active_points
                penalty_points_record.last_violation_at = timezone.now()
                needs_save = True
                logger.info(
                    f"[Recalculate Task] User {user.pk} penalty points updated to {current_total_active_points} for space type {space_type.pk if space_type else 'Global'} (created: {created}).")
            elif created:
                penalty_points_record.last_violation_at = timezone.now()
                needs_save = True
                logger.info(
                    f"[Recalculate Task] New UserPenaltyPointsPerSpaceType record created for user {user.pk} in space type {space_type.pk if space_type else 'Global'} with {current_total_active_points} points.")
            if needs_save:
                penalty_points_record.save(update_fields=['current_penalty_points', 'last_violation_at', 'updated_at'])
            else:
                logger.debug(
                    f"[Recalculate Task] No penalty points update for user {user.pk} in space type {space_type.pk if space_type else 'Global'}. Re-applying ban policy for consistency.")
                _apply_ban_policy(penalty_points_record)
            return ServiceResult.success_result(
                data=penalty_points_record,
                message=f"用户 {user.pk} 在 {space_type.name if space_type else '全局'} 的违约点数已更新并评估禁用策略。"
            )
        except Exception as e:
            logger.exception(
                f"Error in recalculate_and_apply_ban_policies_for_user_and_space_type for user {user.pk}, space type {space_type.pk if space_type else 'Global'}: {e}")
            return self._handle_exception(e, default_message="批处理违约点数和禁用策略失败。")