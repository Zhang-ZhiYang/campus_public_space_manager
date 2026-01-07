# bookings/service/violation_service.py
from django.db import transaction, models  # 导入 models 用于 Q 对象和 Sum 聚合
from django.utils import timezone
from typing import List, Tuple
from django.db.models import QuerySet
from datetime import datetime, timedelta  # 导入 datetime, timedelta

from bookings.models import Violation, UserPenaltyPointsPerSpaceType, SpaceTypeBanPolicy, UserSpaceTypeBan, \
    Booking  # 导入所有需要的模型
from spaces.models import SpaceType  # 从 spaces 应用导入 SpaceType

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException


# Lazy import Space
# from spaces.models import Space, SpaceType # 这些导入现在可以从上面全局导入中获取

class ViolationService(BaseService):
    _dao_map = {
        'violation_dao': 'violation',
    }

    def get_admin_violations_queryset(self, user) -> QuerySet[Violation]:
        # 注意：这里可能需要对 get_violations_for_admin_view 进行进一步优化
        # 以确保它只返回用户有权管理的违规记录
        return self.violation_dao.get_violations_for_admin_view(user)

    def can_manage_violation(self, user, violation: Violation) -> bool:
        if user.is_superuser or user.is_system_admin:
            return True

        # from spaces.models import Space  # 延迟导入，现在可以移除因为 SpaceType 是顶层导入的
        # 使用 DAO 中的辅助方法
        # 假设 self.violation_dao.get_managed_spacetypes_by_user 能够正确返回 SpaceType QuerySet
        managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)

        if violation.space_type:
            return managed_spacetypes.filter(id=violation.space_type.id).exists()
        else:  # Global violation (space_type is None)
            return user.is_superuser or user.is_system_admin

    @transaction.atomic
    def save_violation(self, user, violation_obj: Violation, form_changed_data: List[str]) -> ServiceResult[Violation]:
        try:
            # The logic from save_model in ViolationAdmin
            # 这段逻辑被移动到 signal pre_save 之后，或者可以直接在这里确定 violation_obj.space_type
            # 确保在保存前设置好 space_type
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

            # 使用 DAO 更新对象
            # 关键：这里调用 update 方法时，需要确保它最终会触发 Django 的 save 方法，
            # 从而触发 post_save 信号。如果 DAO 直接使用 QuerySet.update()，则不会触发信号。
            # 通常 DAO 的 update 应该在内部调用 instance.save()。
            updated_violation = self.violation_dao.update(violation_obj, **{
                k: getattr(violation_obj, k) for k in violation_obj.__dict__ if
                k not in ['_state', '_prefetched_objects_cache']  # 提取所有更新过的字段或相关字段
            })
            return ServiceResult.success_result(data=updated_violation, message="违规记录保存成功。")
        except Exception as e:
            return self._handle_exception(e, default_message="保存违规记录失败")

    @transaction.atomic
    def mark_violations_resolved(self, user, queryset: QuerySet[Violation]) -> ServiceResult[Tuple[int, int]]:
        resolved_count = 0
        warnings = []
        errors = []
        for violation in queryset:
            try:
                if self.can_manage_violation(user, violation):
                    if not violation.is_resolved:
                        self.violation_dao.update(
                            violation,
                            is_resolved=True,
                            resolved_by=user,
                            resolved_at=timezone.now()
                        )
                        resolved_count += 1
                    else:
                        warnings.append(f"违规 {violation.id} 已是解决状态。")
                else:
                    errors.append(f"您没有权限解决违规 {violation.id}。")
            except Exception as e:
                errors.append(f"解决违规 {violation.id} 失败: {e}")

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
    def mark_no_show_and_violate(self, user, queryset: QuerySet[Booking]) -> ServiceResult[Tuple[int, int]]:
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
                        else:
                            warnings.append(f"预订 {booking.id} 无法确定空间类型，未能创建违规记录。")
                    except Exception as e:
                        errors.append(f"标记预订 {booking.id} 为未到场或创建违规记录失败: {e}")
                else:
                    warnings.append(f"预订 {booking.id} 状态为 {booking.status} 或未过期，无法标记为未到场。")
            else:
                errors.append(f"您没有权限对预订 {booking.id} 进行未到场标记或创建违规记录。")

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


# --- 以下是移动到此文件的信号处理相关业务逻辑函数 ---

def _get_violation_target_space_type(violation_instance: Violation) -> SpaceType | None:
    """
    辅助函数：确定违规点数应归属的空间类型。
    优先使用 Violation 自身指定的 space_type，否则尝试从关联的 Booking 中获取。
    为了提高效率，并避免在同一个实例生命周期内重复查询，可以缓存结果。
    """
    # 尝试从实例的缓存中获取
    if hasattr(violation_instance, '_cached_space_type'):
        return violation_instance._cached_space_type

    target_space_type = None
    if violation_instance.space_type:
        target_space_type = violation_instance.space_type
    elif violation_instance.booking_id:  # 使用 booking_id 避免不必要的加载
        # 为了获取 space_type，需要加载关联的 Booking 及其 Space/BookableAmenity
        try:
            # 使用 select_related 优化查询，一次性加载所有需要的关联对象
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
            # 如果关联的 Booking 不存在，则无法确定空间类型
            pass

    # 缓存结果
    violation_instance._cached_space_type = target_space_type
    return target_space_type


def _recalculate_user_penalty_points(user: 'CustomUser', space_type: SpaceType | None) -> int:
    """
    私有辅助函数：重新计算用户在特定空间类型下所有未解决的违规点数总和。
    需要导入 CustomUser。
    """
    # 延迟导入 CustomUser 以避免循环依赖，因为 CustomUser 可能会导入 bookings.models
    from users.models import CustomUser

    total_active_points = Violation.objects.filter(
        user=user,
        space_type=space_type,
        is_resolved=False
    ).aggregate(total=models.Sum('penalty_points'))['total'] or 0
    return total_active_points


def _apply_ban_policy(penalty_points_record: UserPenaltyPointsPerSpaceType):
    """
    私有辅助函数：检查用户的活跃违约点数是否达到禁用策略的阈值，并创建/更新禁用记录。
    此函数应在 UserPenaltyPointsPerSpaceType 更新后被调用。
    """
    if not penalty_points_record.user:  # 没有用户则不处理
        return

    # 筛选适用的策略：匹配空间类型（或全局），且点数达到阈值，策略需启用
    applicable_policies = SpaceTypeBanPolicy.objects.filter(
        models.Q(space_type=penalty_points_record.space_type) | models.Q(space_type__isnull=True),
        is_active=True,
        threshold_points__lte=penalty_points_record.current_penalty_points
    ).order_by('-threshold_points', '-priority')  # 点数阈值高的优先，其次是优先级高的

    if applicable_policies.exists():
        policy = applicable_policies.first()  # 选择最匹配（最严厉）的策略
        ban_start = timezone.now()
        ban_end = ban_start + policy.ban_duration

        # 构造禁用记录的 space_type
        # 如果 penalty_points_record.space_type 是 None，则表示全局禁用
        ban_space_type = penalty_points_record.space_type

        # 查找是否存在用户在该空间类型下的活跃禁用记录
        existing_active_ban = UserSpaceTypeBan.objects.filter(
            user=penalty_points_record.user,
            space_type=ban_space_type,  # 适用于特定空间类型或全局禁用
            end_date__gt=timezone.now()  # 必须是当前活跃的禁用
        ).first()

        space_type_name = ban_space_type.name if ban_space_type else '全局'
        reason_message = f"因在 {space_type_name} 累计 {policy.threshold_points} 点触发禁用"

        if existing_active_ban:
            # 如果现有禁用存在，且新策略计算出的禁用结束时间更晚，则延长现有禁用
            if ban_end > existing_active_ban.end_date:
                existing_active_ban.end_date = ban_end
                existing_active_ban.ban_policy_applied = policy
                existing_active_ban.reason = reason_message + "，更新禁用"
                existing_active_ban.save(update_fields=['end_date', 'ban_policy_applied', 'reason'])
        else:
            # 如果没有活跃禁用，则创建新的禁用记录
            UserSpaceTypeBan.objects.create(
                user=penalty_points_record.user,
                space_type=ban_space_type,
                start_date=ban_start,
                end_date=ban_end,
                ban_policy_applied=policy,
                reason=reason_message,
                issued_by=None  # 系统自动触发，执行人员为 None
            )
        # 更新 UserPenaltyPointsPerSpaceType 的最后触发禁用时间
        penalty_points_record.last_ban_trigger_at = ban_start
        penalty_points_record.save(update_fields=['last_ban_trigger_at'])


def handle_violation_save(violation_instance: Violation, created: bool, old_is_resolved: bool, old_penalty_points: int,
                          old_cached_space_type: SpaceType | None):
    """
    业务逻辑：处理 Violation 保存后的逻辑：更新用户违约点数并触发禁用检查。
    此函数由 post_save 信号调用。
    """
    if not violation_instance.user:
        return

    # 确定当前违规所属的空间类型
    current_target_space_type = _get_violation_target_space_type(violation_instance)

    # 获取所有可能受影响的 space_type (当前违规的类型和旧类型)
    affected_space_types = set()
    if current_target_space_type:
        affected_space_types.add(current_target_space_type)
    if old_cached_space_type:  # 即使 old_cached_space_type 为 None 也可能是一个有效的“全局”类型
        affected_space_types.add(old_cached_space_type)

    if not affected_space_types:
        return  # 没有确定的空间类型来操作

    for target_space_type in affected_space_types:
        # 重新计算该用户在该 space_type 下的活跃点数
        current_total_active_points = _recalculate_user_penalty_points(violation_instance.user, target_space_type)

        penalty_points_record, _ = UserPenaltyPointsPerSpaceType.objects.get_or_create(
            user=violation_instance.user,
            space_type=target_space_type
        )

        # 如果点数有变化，则更新并检查禁用策略
        if penalty_points_record.current_penalty_points != current_total_active_points:
            penalty_points_record.current_penalty_points = current_total_active_points
            penalty_points_record.last_violation_at = timezone.now()
            penalty_points_record.save()
            _apply_ban_policy(penalty_points_record)
        # 否则，即使没有点数变化，如果is_resolved或penalty_points实际发生了改变，也应该重新评估禁用策略
        elif created or (not created and (
                violation_instance.is_resolved != old_is_resolved or violation_instance.penalty_points != old_penalty_points)):
            _apply_ban_policy(penalty_points_record)


def handle_violation_delete(violation_instance: Violation):
    """
    业务逻辑：处理 Violation 删除后的逻辑：减少用户活跃违约点数并触发禁用检查。
    此函数由 post_delete 信号调用。
    """
    if not violation_instance.user:  # 没有用户则不处理
        return

    # 删除时，只关心它原来所属的空间类型
    target_space_type = _get_violation_target_space_type(violation_instance)

    if not target_space_type:  # 如果无法确定空间类型，则不处理
        return

    try:
        penalty_points_record = UserPenaltyPointsPerSpaceType.objects.get(
            user=violation_instance.user,
            space_type=target_space_type
        )

        # 重新计算该用户在该 space_type 下的活跃点数 (移除被删除的违规后)
        current_total_active_points = _recalculate_user_penalty_points(violation_instance.user, target_space_type)

        # 如果点数有变化，则更新并检查禁用策略
        if penalty_points_record.current_penalty_points != current_total_active_points:
            penalty_points_record.current_penalty_points = current_total_active_points
            penalty_points_record.last_violation_at = timezone.now()
            penalty_points_record.save()

            _apply_ban_policy(penalty_points_record)  # 检查是否触发禁用 (点数减少也可能触发 ban 状态的变化)
    except UserPenaltyPointsPerSpaceType.DoesNotExist:
        # 如果点数记录不存在，说明此前没有该用户的活跃违规，无需处理
        pass