# bookings/service/violation_service.py
from django.db import transaction, models  # 导入 models 用于 Q 对象和 Sum 聚合
from django.utils import timezone
from typing import List, Tuple, Optional
from django.db.models import QuerySet, Q
from datetime import datetime, timedelta  # 导入 datetime, timedelta
import logging  # 导入 logging 模块

# 获取该模块的 logger 实例
logger = logging.getLogger(__name__)

from bookings.models import Violation, UserPenaltyPointsPerSpaceType, SpaceTypeBanPolicy, UserSpaceTypeBan, Booking
from spaces.models import Space, SpaceType
from users.models import CustomUser

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException


class ViolationService(BaseService):
    _dao_map = {
        'violation_dao': 'violation',
        'booking_dao': 'booking',  # Ensure booking_dao is available for _check_booking_permission
    }

    def get_admin_violations_queryset(self, user: CustomUser) -> QuerySet[Violation]:
        return self.violation_dao.get_violations_for_admin_view(user)

    def can_manage_violation(self, user: CustomUser, violation: Violation) -> bool:
        if user.is_superuser or user.is_system_admin:
            return True

        managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)

        if violation.space_type:
            return managed_spacetypes.filter(id=violation.space_type.id).exists()
        else:  # Global violation (space_type is None)
            return user.is_superuser or user.is_system_admin

    @transaction.atomic
    def save_violation(self, user: CustomUser, violation_obj: Violation, form_changed_data: List[str]) -> ServiceResult[
        Violation]:
        try:
            # 如果 Violation 实例本身没有 space_type，应尝试从 booking 关联推断
            if not violation_obj.space_type and violation_obj.booking:
                if violation_obj.booking.space and violation_obj.booking.space.space_type:
                    violation_obj.space_type = violation_obj.booking.space.space_type
                elif violation_obj.booking.bookable_amenity and violation_obj.booking.bookable_amenity.space \
                        and violation_obj.booking.bookable_amenity.space.space_type:
                    violation_obj.space_type = violation_obj.bookable_amenity.space.space_type

            if not self.can_manage_violation(user, violation_obj):
                raise ForbiddenException(f"您没有权限修改此违规记录(ID: {violation_obj.pk})。")

            if 'is_resolved' in form_changed_data:
                if violation_obj.is_resolved and not violation_obj.resolved_at:
                    violation_obj.resolved_at = timezone.now()
                    violation_obj.resolved_by = user
                elif not violation_obj.is_resolved and violation_obj.resolved_at:
                    violation_obj.resolved_at = None
                    violation_obj.resolved_by = None

            updated_violation = self.violation_dao.update_violation(violation_obj, **{
                k: getattr(violation_obj, k) for k in violation_obj.__dict__ if
                k not in ['_state', '_prefetched_objects_cache']  # 提取所有更新过的字段或相关字段
            })
            return ServiceResult.success_result(data=updated_violation, message="违规记录保存成功。")
        except Exception as e:
            logger.error(f"Error saving violation {getattr(violation_obj, 'pk', 'new')}: {e}", exc_info=True)
            return self._handle_exception(e, default_message=f"保存违规记录失败: {e}")

    @transaction.atomic
    def mark_violations_resolved(self, user: CustomUser, queryset: QuerySet[Violation]) -> ServiceResult[
        Tuple[int, int]]:
        resolved_count = 0
        warnings = []
        errors = []
        for violation in queryset:
            try:
                if self.can_manage_violation(user, violation):
                    if not violation.is_resolved:
                        # 使用 DAO 的 update 方法来更新实例，这将触发 post_save 信号
                        self.violation_dao.update_violation(
                            violation,
                            is_resolved=True,
                            resolved_by=user,
                            resolved_at=timezone.now()
                        )
                        resolved_count += 1
                        logger.info(f"Violation {violation.id} marked as resolved by user {user.id}.")
                    else:
                        warnings.append(f"违规 {violation.id} 已是解决状态。")
                        logger.debug(f"Violation {violation.id} already resolved, no action taken.")
                else:
                    errors.append(f"您没有权限解决违规 {violation.id}。")
                    logger.warning(f"User {user.id} attempted to resolve violation {violation.id} without permission.")
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
    def mark_no_show_and_violate(self, user: CustomUser, queryset: QuerySet[Booking]) -> ServiceResult[Tuple[int, int]]:
        no_show_count = 0
        violation_count = 0
        warnings = []
        errors = []
        # Lazy import Space if not already imported globally
        # from spaces.models import Space
        for booking in queryset:
            if self._check_booking_permission(user, booking, 'spaces.can_manage_space_bookings') or \
                    user.has_perm('bookings.can_check_in_booking'):
                if booking.status in ['PENDING', 'APPROVED'] and booking.end_time < timezone.now():
                    try:
                        self.booking_dao.update(booking, status='NO_SHOW')
                        no_show_count += 1

                        target_space = self.booking_dao.get_target_space_for_booking(booking)
                        space_type_for_violation = target_space.space_type if target_space else None
                        if space_type_for_violation:
                            # 这里的 create_violation 会触发 signal，最终调用 handle_violation_save
                            self.violation_dao.create_violation(
                                user=booking.user,
                                booking=booking,
                                space_type=space_type_for_violation,
                                violation_type='NO_SHOW',
                                description=f"用户 {booking.user.get_full_name} 未在 {getattr(target_space, 'name', '未知空间')} 预订中签到。",
                                issued_by=user,
                                penalty_points=1
                            )
                            violation_count += 1
                            logger.info(
                                f"Violation created for booking {booking.id} (user {booking.user.id}, space_type {space_type_for_violation.id}).")
                        else:
                            warnings.append(f"预订 {booking.id} 无法确定空间类型，未能创建违规记录。")
                            logger.warning(
                                f"Booking {booking.id} (user {booking.user.id}) has no discernable space type, skipping violation creation.")
                    except Exception as e:
                        errors.append(f"标记预订 {booking.id} 为未到场或创建违规记录失败: {e}")
                        logger.error(f"Error marking booking {booking.id} as no-show or creating violation: {e}",
                                     exc_info=True)
                else:
                    warnings.append(f"预订 {booking.id} 状态为 {booking.status} 或未过期，无法标记为未到场。")
                    logger.debug(
                        f"Booking {booking.id} status is {booking.status} or not expired, cannot mark as no-show.")
            else:
                errors.append(f"您没有权限对预订 {booking.id} 进行未到场标记或创建违规记录。")
                logger.warning(f"User {user.id} has no permission for no-show or violation on booking {booking.id}.")

        if errors:
            return ServiceResult.error_result(
                message="部分预订操作失败", errors=errors + warnings,
                error_code=BadRequestException.default_code, status_code=BadRequestException.status_code
            )
        return ServiceResult.success_result(
            data=(no_show_count, violation_count),
            message=f"成功标记 {no_show_count} 条预订为未到场，创建 {violation_count} 条违规记录。",
            warnings=warnings
        )

    def _check_booking_permission(self, user: CustomUser, booking: Booking, permission_codename: str) -> bool:
        from spaces.models import Space  # 延迟导入
        target_space = self.booking_dao.get_target_space_for_booking(booking)
        return user.is_superuser or user.is_system_admin or \
            (target_space and user.has_perm(permission_codename, target_space))


# --- 以下是移动到此文件的信号处理相关业务逻辑函数 ---
# 这些函数是模块级别的，不属于 ViolationService 类

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

    # 缓存结果
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

    # 1. 查找当前活跃的禁用记录
    existing_active_ban = UserSpaceTypeBan.objects.filter(
        user=ban_user,
        space_type=ban_space_type,  # 适用于特定空间类型或全局禁用
        end_date__gt=timezone.now()  # 必须是当前活跃的禁用
    ).first()

    # 2. 查找当前点数下最严格的适用策略
    applicable_policies = SpaceTypeBanPolicy.objects.filter(
        Q(space_type=ban_space_type) | Q(space_type__isnull=True),
        is_active=True,
        threshold_points__lte=current_points  # 点数达到阈值
    ).order_by('-threshold_points', '-priority')  # 点数阈值高的优先，其次是优先级高的

    policy_to_apply = applicable_policies.first()

    if policy_to_apply:
        # 情况 A: 存在适用的策略 (点数达到阈值或更高)
        ban_start = timezone.now()
        ban_end = ban_start + policy_to_apply.ban_duration
        reason_message = f"因在 {space_type_name} 累计 {policy_to_apply.threshold_points} 点触发禁用"

        if existing_active_ban:
            # 如果当前有活跃禁用，检查是否需要延长
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
            # 没有活跃禁用，创建新的
            UserSpaceTypeBan.objects.create(
                user=ban_user,
                space_type=ban_space_type,
                start_date=ban_start,
                end_date=ban_end,
                ban_policy_applied=policy_to_apply,
                reason=reason_message,
                issued_by=None  # 系统自动触发，执行人员为 None
            )
            logger.info(
                f"New ban created for user {ban_user.id} in {space_type_name} until {ban_end.strftime('%Y-%m-%d %H:%M')}.")

        # 更新 UserPenaltyPointsPerSpaceType 的最后触发禁用时间
        penalty_points_record.last_ban_trigger_at = ban_start
        penalty_points_record.save(update_fields=['last_ban_trigger_at'])

    else:
        # 情况 B: 没有适用的策略 (点数低于所有阈值)
        if existing_active_ban:
            # 如果存在活跃禁用，但点数已低于所有策略阈值，则立即结束禁用
            original_end_date = existing_active_ban.end_date
            existing_active_ban.end_date = timezone.now()
            existing_active_ban.reason += f" (自动解除: 点数降至 {current_points}，低于所有禁用策略阈值)"
            existing_active_ban.save(update_fields=['end_date', 'reason'])
            logger.info(
                f"Ban for user {ban_user.id} in {space_type_name} automatically lifted. Was set until {original_end_date.strftime('%Y-%m-%d %H:%M')}.")
        else:
            logger.debug(
                f"No applicable ban policy and no existing active ban for user {ban_user.id} in {space_type_name}.")

    # 注意：last_ban_trigger_at 主要记录禁用被触发的时间。当禁用被解除时，我们通常不修改这个字段，
    # 除非语义上需要记录 "最后一次导致禁用状态变化的日期"。目前保持只在触发时更新。


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

    # 确定当前违规所属的空间类型
    current_target_space_type = _get_violation_target_space_type(violation_instance)

    # 获取所有可能受影响的 space_type (当前违规的类型和旧类型)
    # 这确保了如果违规的空间类型改变，两个相关的 UserPenaltyPointsPerSpaceType 记录都会被更新
    affected_space_types = set()
    if current_target_space_type is not None:  # 明确检查 None
        affected_space_types.add(current_target_space_type)
    if old_cached_space_type is not None:  # 即使 old_cached_space_type 为 None 也可能是一个有效的“全局”类型
        affected_space_types.add(old_cached_space_type)

    if not affected_space_types:
        logger.warning(
            f"Violation {violation_instance.pk if violation_instance.pk else 'new'} cannot determine a target space type, skipping penalty points update.")
        return  # 没有确定的空间类型来操作

    for target_space_type in affected_space_types:
        # 重新计算该用户在该 space_type 下的活跃点数
        current_total_active_points = _recalculate_user_penalty_points(violation_instance.user, target_space_type)

        penalty_points_record, created_pp = UserPenaltyPointsPerSpaceType.objects.get_or_create(
            user=violation_instance.user,
            space_type=target_space_type
        )
        if created_pp:
            logger.info(
                f"Created new UserPenaltyPointsPerSpaceType record for user {violation_instance.user.id} in space type {target_space_type.id if target_space_type else 'Global'}.")

        # 如果点数有变化，则更新并检查禁用策略
        if penalty_points_record.current_penalty_points != current_total_active_points:
            logger.info(
                f"User {violation_instance.user.id} penalty points changed from {penalty_points_record.current_penalty_points} to {current_total_active_points} in space type {target_space_type.id if target_space_type else 'Global'}.")
            penalty_points_record.current_penalty_points = current_total_active_points
            penalty_points_record.last_violation_at = timezone.now()
            penalty_points_record.save()
            _apply_ban_policy(penalty_points_record)
        # 如果点数没有变化，但违规状态（is_resolved）或点数本身（penalty_points）改变，
        # 且可能影响到禁用策略（例如，从已解决变为未解决，或者点数变化但总和不变），
        # 还要重新评估禁用策略。
        elif created or \
                (not created and (violation_instance.is_resolved != old_is_resolved or \
                                  violation_instance.penalty_points != old_penalty_points or \
                                  current_target_space_type != old_cached_space_type)):  # 考虑空间类型变更
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

    # 删除时，只关心它原来所属的空间类型
    target_space_type = _get_violation_target_space_type(violation_instance)

    if target_space_type is None:  # 如果无法确定空间类型，则不处理
        logger.warning(
            f"Violation {violation_instance.pk} deleted was unable to determine its space type, skipping penalty points update.")
        return

    try:
        penalty_points_record = UserPenaltyPointsPerSpaceType.objects.get(
            user=violation_instance.user,
            space_type=target_space_type
        )

        # 重新计算该用户在该 space_type 下的活跃点数 (移除被删除的违规后)
        current_total_active_points = _recalculate_user_penalty_points(violation_instance.user, target_space_type)

        # 始终更新点数并检查禁用策略，因为删除一个违规本身就是一种变化
        if penalty_points_record.current_penalty_points != current_total_active_points:
            logger.info(
                f"User {violation_instance.user.id} penalty points changed from {penalty_points_record.current_penalty_points} to {current_total_active_points} after deleting violation {violation_instance.pk} in space type {target_space_type.id if target_space_type else 'Global'}.")
            penalty_points_record.current_penalty_points = current_total_active_points
            penalty_points_record.last_violation_at = timezone.now()  # 这里可以更新为删除时间，或者设置为None如果不再有违规
            penalty_points_record.save()

        else:
            logger.debug(
                f"User {violation_instance.user.id} penalty points not changed after deleting violation {violation_instance.pk} in space type {target_space_type.id if target_space_type else 'Global'}.")

        _apply_ban_policy(penalty_points_record)  # 即使点数没变，也重新评估一次，以确保删除后状态的正确性

    except UserPenaltyPointsPerSpaceType.DoesNotExist:
        logger.info(
            f"No UserPenaltyPointsPerSpaceType record found for user {violation_instance.user.id} in space type {target_space_type.id if target_space_type else 'Global'} after deleting violation {violation_instance.pk}.")
        pass