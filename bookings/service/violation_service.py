# bookings/service/violation_service.py (终极修订版)
from django.contrib.contenttypes.models import ContentType
from django.db import transaction, models
from django.utils import timezone
from typing import List, Tuple, Optional, Dict, Any
from django.db.models import QuerySet, Q
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

from bookings.models import Violation, UserPenaltyPointsPerSpaceType, SpaceTypeBanPolicy, UserSpaceTypeBan, Booking
from spaces.models import Space, SpaceType
from users.models import CustomUser

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException
from guardian.shortcuts import get_objects_for_user, assign_perm

# ====================================================================
# 模块级别的辅助函数 (为了避免循环引用和保持 Service 类的纯净)
# ====================================================================

def _get_violation_target_space_type(violation_instance: Violation) -> SpaceType | None:
    """
    辅助函数：确定违规点数应归属的空间类型。
    优先使用 Violation 自身指定的 space_type，否则尝试从关联的 Booking 中获取。
    为了提高效率，并避免在同一个实例生命周期内重复查询，可以缓存结果。
    """
    if hasattr(violation_instance, '_cached_space_type'):
        return violation_instance._cached_space_type

    target_space_type = None
    if violation_instance.space_type:
        target_space_type = violation_instance.space_type
    elif violation_instance.booking_id:
        try:
            booking_obj = Booking.objects.select_related(
                'space__space_type',
                'bookable_amenity__space__space_type'
            ).get(pk=violation_instance.booking_id)

            if booking_obj.space and booking_obj.space.space_type:
                target_space_type = booking_obj.space.space_type
            elif booking_obj.bookable_amenity and \
                    booking_obj.bookable_amenity.space and \
                    booking_obj.bookable_amenity.space.space_type:
                target_space_type = booking_obj.bookable_amenity.space.space_type
        except Booking.DoesNotExist:
            logger.debug(
                f"Booking {violation_instance.booking_id} not found for violation {violation_instance.pk if violation_instance.pk else 'new'}, cannot infer space type.")
            pass

    violation_instance._cached_space_type = target_space_type
    return target_space_type

def _recalculate_user_penalty_points(user: CustomUser, space_type: SpaceType | None) -> int:
    """
    私有辅助函数：重新计算用户在特定空间类型下所有未解决的违规点数总和。
    """
    total_active_points = Violation.objects.filter(
        user=user,
        space_type=space_type,
        is_resolved=False
    ).aggregate(total=models.Sum('penalty_points'))['total'] or 0
    logger.debug(
        f"Recalculated penalty points for user {user.id} in space type {space_type.id if space_type else 'Global'}: {total_active_points}")
    return total_active_points

def _apply_ban_policy(penalty_points_record: UserPenaltyPointsPerSpaceType):
    """
    私有辅助函数：检查用户的活跃违约点数是否达到禁用策略的阈值，并创建/更新/解除禁用记录。
    此函数应在 UserPenaltyPointsPerSpaceType 更新后被调用。
    """
    if not penalty_points_record.user:
        return

    ban_user = penalty_points_record.user
    ban_space_type = penalty_points_record.space_type
    space_type_name = ban_space_type.name if ban_space_type else '全局'
    current_points = penalty_points_record.current_penalty_points

    logger.debug(f"Evaluating ban policy for user {ban_user.id} in {space_type_name} with {current_points} points.")

    existing_active_ban = UserSpaceTypeBan.objects.filter(
        user=ban_user,
        space_type=ban_space_type,
        end_date__gt=timezone.now()
    ).first()

    applicable_policies = SpaceTypeBanPolicy.objects.filter(
        Q(space_type=ban_space_type) | Q(space_type__isnull=True),
        is_active=True,
        threshold_points__lte=current_points
    ).order_by('-threshold_points', '-priority')

    policy_to_apply = applicable_policies.first()

    if policy_to_apply:
        ban_start = timezone.now()
        ban_end = ban_start + policy_to_apply.ban_duration
        reason_message = f"因在 {space_type_name} 累计 {policy_to_apply.threshold_points} 点触发禁用"

        if existing_active_ban:
            if ban_end > existing_active_ban.end_date:
                existing_active_ban.end_date = ban_end
                existing_active_ban.ban_policy_applied = policy_to_apply
                existing_active_ban.reason = reason_message + "，更新延长禁用"
                existing_active_ban.save(update_fields=['end_date', 'ban_policy_applied', 'reason'])
                logger.info(
                    f"Ban extended for user {ban_user.id} in {space_type_name} until {ban_end.strftime('%Y-%m-%d %H:%M')}.")
            else:
                logger.debug(
                    f"Existing ban for user {ban_user.id} in {space_type_name} is already longer or equal, no extension needed.")
        else:
            UserSpaceTypeBan.objects.create(
                user=ban_user,
                space_type=ban_space_type,
                start_date=ban_start,
                end_date=ban_end,
                ban_policy_applied=policy_to_apply,
                reason=reason_message,
                issued_by=None
            )
            logger.info(
                f"New ban created for user {ban_user.id} in {space_type_name} until {ban_end.strftime('%Y-%m-%d %H:%M')}.")

        penalty_points_record.last_ban_trigger_at = ban_start
        penalty_points_record.save(update_fields=['last_ban_trigger_at'])

    else:
        if existing_active_ban:
            original_end_date = existing_active_ban.end_date
            existing_active_ban.end_date = timezone.now()
            existing_active_ban.reason += f" (自动解除: 点数降至 {current_points}，低于所有禁用策略阈值)"
            existing_active_ban.save(update_fields=['end_date', 'reason'])
            logger.info(
                f"Ban for user {ban_user.id} in {space_type_name} automatically lifted. Was set until {original_end_date.strftime('%Y-%m-%d %H:%M')}.")
        else:
            logger.debug(
                f"No applicable ban policy and no existing active ban for user {ban_user.id} in {space_type_name}.")

def handle_violation_save(violation_instance: Violation, created: bool, old_is_resolved: bool, old_penalty_points: int,
                          old_cached_space_type: SpaceType | None):
    """
    业务逻辑：处理 Violation 保存后的逻辑：更新用户违约点数并触发禁用检查。
    此函数由 post_save 信号调用。
    """
    if not violation_instance.user:
        logger.warning(
            f"Violation {violation_instance.pk if violation_instance.pk else 'new'} has no associated user, skipping penalty points update.")
        return

    current_target_space_type = _get_violation_target_space_type(violation_instance)

    affected_space_types = set()
    if current_target_space_type is not None:
        affected_space_types.add(current_target_space_type)
    if old_cached_space_type is not None:
        affected_space_types.add(old_cached_space_type)

    if not affected_space_types:
        logger.warning(
            f"Violation {violation_instance.pk if violation_instance.pk else 'new'} cannot determine a target space type, skipping penalty points update.")
        return

    for target_space_type in affected_space_types:
        current_total_active_points = _recalculate_user_penalty_points(violation_instance.user, target_space_type)

        penalty_points_record, created_pp = UserPenaltyPointsPerSpaceType.objects.get_or_create(
            user=violation_instance.user,
            space_type=target_space_type
        )
        if created_pp:
            logger.info(
                f"Created new UserPenaltyPointsPerSpaceType record for user {violation_instance.user.id} in space type {target_space_type.id if target_space_type else 'Global'}.")

        if penalty_points_record.current_penalty_points != current_total_active_points:
            logger.info(
                f"User {violation_instance.user.id} penalty points changed from {penalty_points_record.current_penalty_points} to {current_total_active_points} in space type {target_space_type.id if target_space_type else 'Global'}.")
            penalty_points_record.current_penalty_points = current_total_active_points
            penalty_points_record.last_violation_at = timezone.now()
            penalty_points_record.save()
            _apply_ban_policy(penalty_points_record)
        elif created or \
                (not created and (violation_instance.is_resolved != old_is_resolved or \
                                  violation_instance.penalty_points != old_penalty_points or \
                                  current_target_space_type != old_cached_space_type)):
            logger.debug(
                f"Violation {violation_instance.pk} updated (status/points/space_type changed without affecting total points in {target_space_type.id if target_space_type else 'Global'}), re-evaluating ban policy.")
            _apply_ban_policy(penalty_points_record)
        else:
            logger.debug(
                f"No significant change detected for penalty points and ban policy for user {violation_instance.user.id} in space type {target_space_type.id if target_space_type else 'Global'}.")

def handle_violation_delete(violation_instance: Violation):
    """
    业务逻辑：处理 Violation 删除后的逻辑：减少用户活跃违约点数并触发禁用检查。
    此函数由 post_delete 信号调用。
    """
    if not violation_instance.user:
        logger.warning(
            f"Violation {violation_instance.pk} deleted has no associated user, skipping penalty points update.")
        return

    target_space_type = _get_violation_target_space_type(violation_instance)

    if target_space_type is None:
        logger.warning(
            f"Violation {violation_instance.pk} deleted was unable to determine its space type, skipping penalty points update.")
        return

    try:
        penalty_points_record = UserPenaltyPointsPerSpaceType.objects.get(
            user=violation_instance.user,
            space_type=target_space_type
        )

        current_total_active_points = _recalculate_user_penalty_points(violation_instance.user, target_space_type)

        if penalty_points_record.current_penalty_points != current_total_active_points:
            logger.info(
                f"User {violation_instance.user.id} penalty points changed from {penalty_points_record.current_penalty_points} to {current_total_active_points} after deleting violation {violation_instance.pk} in space type {target_space_type.id if target_space_type else 'Global'}.")
            penalty_points_record.current_penalty_points = current_total_active_points
            # 注意：这里可以根据业务需求决定是否更新 last_violation_at
            # penalty_points_record.last_violation_at = timezone.now()
            penalty_points_record.save()

        else:
            logger.debug(
                f"User {violation_instance.user.id} penalty points not changed after deleting violation {violation_instance.pk} in space type {target_space_type.id if target_space_type else 'Global'}.")

        _apply_ban_policy(penalty_points_record)

    except UserPenaltyPointsPerSpaceType.DoesNotExist:
        logger.info(
            f"No UserPenaltyPointsPerSpaceType record found for user {violation_instance.user.id} in space type {target_space_type.id if target_space_type else 'Global'} after deleting violation {violation_instance.pk}.")
        pass

class ViolationService(BaseService):
    _dao_map = {
        'violation_dao': 'violation',
        'booking_dao': 'booking',
    }

    # get_admin_violations_queryset: 保持现状，但更新权限检查以匹配新的权限定义
    def get_admin_violations_queryset(self, user: CustomUser) -> ServiceResult[QuerySet[Violation]]:
        """
        根据用户权限获取适用于 Admin 视图的违规记录 QuerySet。
        视图层确保用户已认证并通过角色检查 `@is_admin_or_space_manager_required`。
        Service 层负责根据细粒度权限和对象属性过滤数据。
        """
        # 系统管理员拥有最高权限，可查看所有违规记录
        if user.is_superuser or user.is_system_admin:
            return ServiceResult.success_result(
                data=self.violation_dao.get_queryset(),
                message="成功获取所有违规记录。"
            )

        # 如果用户拥有查看所有违规记录的全局权限
        if user.is_authenticated and user.has_perm('bookings.can_view_all_violations'):
            return ServiceResult.success_result(
                data=self.violation_dao.get_queryset(),
                message="成功获取所有违规记录。"
            )

        # 空间管理员可以查看其管理的空间类型相关的违规记录
        # 这里的逻辑是：如果空间管理员可以管理某个空间（spaces.can_edit_space_info），
        # 那么他们应该可以查看该空间类型下的违规记录
        if user.is_authenticated and user.is_space_manager:
            space_ct = ContentType.objects.get_for_model(Space)
            managed_spaces = get_objects_for_user(
                user, 'spaces.can_view_space', klass=Space  # 可以查看的空间
            )
            # 扩展：也可以通过 managed_by 字段直接关联的空间类型
            managed_spacetype_ids = []
            for space in managed_spaces:
                if space.space_type:
                    managed_spacetype_ids.append(space.space_type.id)

            # 同时考虑用户直接被授予 can_view_violation_record 的违规对象
            explicitly_viewable_violations = get_objects_for_user(
                user, 'bookings.can_view_violation_record', klass=Violation
            )

            # 过滤违规记录：
            # 1. 违规记录直接关联的空间类型在用户管理的类型中
            # 2. 违规记录的预订目标（空间或设施所在空间）的空间类型在用户管理的类型中
            # 3. 用户直接被授予了查看权限的违规记录
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

        # 默认情况下，无权限查看违规记录
        return ServiceResult.error_result(
            message="您没有权限查看违规记录。",
            error_code=ForbiddenException.default_code,
            status_code=ForbiddenException.status_code
        )

    # save_violation: 细粒度权限检查
    @transaction.atomic
    def save_violation(self, user: CustomUser, violation_data: Dict[str, Any]) -> ServiceResult[Violation]:
        """
        创建新的违规记录或更新现有记录。
        视图层确保用户已认证并通过角色检查。Service层在此进行细粒度权限检查。
        """
        violation_id = violation_data.get('id')
        violation_obj = None

        # --- 统一推断 space_type 逻辑 ---
        # NOTE: 此处的 `booking` 应该是一个 Booking 实例，而不是 ID。
        # 如果 `violation_data['booking']` 是一个 ID，需要在 Service 层转换为实例。
        # 这里假设 `violation_data['booking']` 已经是实例。
        booking_instance_from_data = violation_data.get('booking')
        if not violation_data.get('space_type') and booking_instance_from_data:
            if booking_instance_from_data.space and booking_instance_from_data.space.space_type:
                violation_data['space_type'] = booking_instance_from_data.space.space_type
            elif booking_instance_from_data.bookable_amenity and booking_instance_from_data.bookable_amenity.space \
                    and booking_instance_from_data.bookable_amenity.space.space_type:
                violation_data['space_type'] = booking_instance_from_data.bookable_amenity.space.space_type

        if violation_id:
            violation_obj = self.violation_dao.get_violation_by_id(violation_id)
            if not violation_obj:
                return ServiceResult.error_result(
                    message="违规记录未找到。", error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # --- 权限检查：编辑现有违规记录 ---
            # 系统管理员拥有全局编辑权限
            if not user.is_system_admin:
                # 检查用户是否拥有全局编辑违规记录权限
                if user.has_perm('bookings.can_edit_violation_record'):  # 全局编辑
                    pass  # 有全局权限，允许编辑
                # 或者，如果是非全局管理员，检查是否是该记录所属空间类型的管理员
                elif violation_obj.space_type and user.is_space_manager:
                    # 检查 SpaceManager 是否有权限编辑其管理 SpaceType 下的违规
                    # 简化为：如果 SpaceManager 可以管理此违规所属 SpaceType 下的任何 Space，则可以编辑
                    managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)
                    if not managed_spacetypes.filter(id=violation_obj.space_type.id).exists():
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无编辑此空间类型违规记录权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                else:  # 没有权限编辑
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (无编辑违规记录权限)",
                        error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                    )
        else:
            # --- 权限检查：创建新违规记录 ---
            # 系统管理员拥有全局创建权限
            if not user.is_system_admin:
                # 检查用户是否拥有全局创建违规记录权限
                if not user.has_perm('bookings.can_create_violation_record'):
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (无创建违规记录权限)",
                        error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                    )
                # 如果是空间管理员，但没有全局创建权限，可以检查是否能为自己管理的空间类型创建违规
                if user.is_space_manager and 'space_type' in violation_data and violation_data['space_type']:
                    managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)
                    if not managed_spacetypes.filter(id=violation_data['space_type'].id).exists():
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无创建此空间类型违规记录权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                elif user.is_space_manager and 'space_type' not in violation_data:  # 无法推断空间类型，无法判断权限
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (无法确定空间类型，无权创建违规记录)",
                        error_code=BadRequestException.default_code, status_code=BadRequestException.status_code
                    )
                elif not user.is_space_manager:  # 非系统管理员也非空间管理员，没有创建权限
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (无创建违规记录权限)",
                        error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                    )

        try:
            if violation_id:
                # Store old attributes for signal processing
                violation_obj._old_is_resolved = violation_obj.is_resolved
                violation_obj._old_penalty_points = violation_obj.penalty_points
                # Correctly call the module-level helper function
                violation_obj._old_cached_space_type = _get_violation_target_space_type(violation_obj)

                resolved_changed = 'is_resolved' in violation_data and violation_obj.is_resolved != violation_data[
                    'is_resolved']

                for key, value in violation_data.items():
                    setattr(violation_obj, key, value)

                if resolved_changed:
                    if violation_obj.is_resolved and not violation_obj.resolved_at:
                        violation_obj.resolved_at = timezone.now()
                        violation_obj.resolved_by = user
                    elif not violation_obj.is_resolved:
                        violation_obj.resolved_at = None
                        violation_obj.resolved_by = None

                updated_violation = self.violation_dao.update(violation_obj, **violation_data)
                return ServiceResult.success_result(data=updated_violation, message="违规记录更新成功。")
            else:
                new_violation = self.violation_dao.create_violation(user=violation_data['user'], **violation_data)
                # Assign initial object-level permission for the creator if they are a SpaceManager
                if user.is_space_manager and new_violation.space_type:
                    assign_perm('bookings.can_edit_violation_record', user, new_violation)
                    assign_perm('bookings.can_resolve_violation_record', user, new_violation)
                return ServiceResult.success_result(data=new_violation, message="违规记录创建成功。", status_code=201)
        except Exception as e:
            logger.error(f"Error saving violation {violation_id if violation_id else 'new'}: {e}", exc_info=True)
            return self._handle_exception(e, default_message=f"保存违规记录失败: {e}")

    # mark_violations_resolved: 细粒度权限检查
    @transaction.atomic
    def mark_violations_resolved(self, user: CustomUser, pk_list: List[int]) -> ServiceResult[Tuple[int, int]]:
        """
        标记一批违规记录为已解决。
        视图层确保用户已认证并通过角色检查 `@is_admin_or_space_manager_required`。
        Service层在此进行细粒度权限检查。
        """
        resolved_count = 0
        warnings = []
        errors = []

        # 首先检查用户是否拥有全局解决权限
        has_global_resolve_perm = user.is_system_admin or user.has_perm('bookings.can_resolve_violation_record')

        queryset = self.violation_dao.filter(pk__in=pk_list)

        for violation in queryset:
            # 如果没有全局解决权限，则检查对象级权限
            if not has_global_resolve_perm and not user.has_perm('bookings.can_resolve_violation_record', violation):
                errors.append(f"您没有权限解决违规 {violation.id}。")
                logger.warning(
                    f"User {user.id} attempted to resolve violation {violation.id} without sufficient permission.")
                continue  # 跳过当前违规

            try:
                if not violation.is_resolved:
                    self.violation_dao.update_violation(
                        violation,
                        is_resolved=True, resolved_by=user, resolved_at=timezone.now()
                    )
                    resolved_count += 1
                    logger.info(f"Violation {violation.id} marked as resolved by user {user.id}.")
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

    # mark_no_show_and_violate: 细粒度权限检查
    @transaction.atomic
    def mark_no_show_and_violate(self, user: CustomUser, pk_list: List[int]) -> ServiceResult[Tuple[int, int]]:
        """
        标记一批预订为未到场并创建违规记录。
        视图层 `@is_admin_or_space_manager_required` 确保了用户角色。
        Service层在此进行细粒度权限检查。
        这个方法将同时检查“标记未到场”的权限和“创建违规记录”的权限。
        """
        no_show_count = 0
        violation_count = 0
        warnings = []
        errors = []

        # 预先检查用户是否拥有全局的“标记未到场并创建违规”权限
        has_global_mark_no_show_and_violate_perm = user.is_system_admin or \
                                                    user.has_perm('bookings.can_mark_no_show_and_create_violation')
        # 预先检查用户是否拥有全局的“创建违规记录”权限 (用于违规创建部分)
        has_global_create_violation_perm = user.is_system_admin or \
                                           user.has_perm('bookings.can_create_violation_record')

        queryset = self.booking_dao.filter(pk__in=pk_list)

        for booking in queryset:
            target_space = self.booking_dao.get_target_space_for_booking(booking)

            # --- 权限检查 for Marking No-Show ---
            can_mark_single_no_show = has_global_mark_no_show_and_violate_perm or \
                                      (target_space and user.has_perm('spaces.can_checkin_space_bookings',
                                                                      target_space))

            # --- 权限检查 for Creating Violation Record ---
            can_create_single_violation_record = has_global_create_violation_perm
            # 或者，如果空间管理员可以为其管理的空间类型创建违规记录
            # `self.violation_dao.get_managed_spacetypes_by_user(user)` 要求 Service 实例
            if not can_create_single_violation_record and user.is_space_manager and target_space and target_space.space_type:
                managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)
                if managed_spacetypes.filter(id=target_space.space_type.id).exists():
                    can_create_single_violation_record = True

            if not can_mark_single_no_show:
                errors.append(f"您没有权限对预订 {booking.id} 进行未到场标记。")
                logger.warning(f"User {user.id} has no permission to mark no-show for booking {booking.id}.")
                continue  # 跳过当前预订

            # 业务逻辑：状态和时间检查 (保持不变)
            if booking.status in ['PENDING', 'APPROVED'] and booking.end_time < timezone.now():
                try:
                    self.booking_dao.update(booking, status='NO_SHOW')
                    no_show_count += 1

                    space_type_for_violation = None
                    if target_space:
                        space_type_for_violation = target_space.space_type

                    if space_type_for_violation and can_create_single_violation_record:
                        # DAO 调用 create_violation 方法，该方法接收 user, booking, space_type 等参数
                        self.violation_dao.create_violation(
                            user=booking.user, booking=booking, space_type=space_type_for_violation,
                            violation_type='NO_SHOW',
                            description=f"用户 {booking.user.get_full_name} 未在 {getattr(target_space, 'name', '未知空间')} 预订中签到。",
                            issued_by=user, penalty_points=1 # issued_by 是当前操作的用户
                        )
                        violation_count += 1
                        logger.info(
                            f"Violation created for booking {booking.id} (user {booking.user.id}, space_type {space_type_for_violation.id}).")
                    elif space_type_for_violation and not can_create_single_violation_record:
                        warnings.append(f"用户 {user.username} 无权为预订 {booking.id} 创建违规记录，已跳过。")
                    else:
                        warnings.append(f"预订 {booking.id} 无法确定空间类型，未能创建违规记录。")

                except Exception as e:
                    errors.append(f"标记预订 {booking.id} 为未到场或创建违规记录失败: {e}")
                    logger.error(f"Error marking booking {booking.id} as no-show or creating violation: {e}",
                                 exc_info=True)
            else:
                warnings.append(f"预订 {booking.id} 状态为 {booking.status} 或未过期，无法标记为未到场。")

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