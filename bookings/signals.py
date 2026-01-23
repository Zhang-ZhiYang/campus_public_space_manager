# bookings/signals.py
import logging
from datetime import timedelta
from typing import Optional, Set

from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.utils import timezone  # 导入 timezone
from django.db import models  # New import for Sum
from django.db.models import QuerySet, Q  # New import for Q
from typing import Optional, Set  # New import for typing

from bookings.models import (
    Booking, Space, UserSpaceTypeBan, UserSpaceTypeExemption,
    Violation, UserPenaltyPointsPerSpaceType, SpaceTypeBanPolicy  # New imports for Violation, Penalty, Ban Policy
)
from core.service.factory import ServiceFactory
from bookings.tasks.booking_tasks import booking_cache_invalidation_task
from bookings.tasks.ban_tasks import ban_cache_invalidation_task
from bookings.tasks.exemption_tasks import exemption_cache_invalidation_task

# 导入 CustomUser 模型和 SpaceType
from users.models import CustomUser
from spaces.models import SpaceType

logger = logging.getLogger(__name__)

# --- 模块级别的辅助函数 ---
# 这些函数被信号处理器调用，以避免循环依赖和保持代码组织性

def _get_violation_target_space_type(violation_instance: Violation) -> Optional[SpaceType]:
    """
    辅助函数：确定违规点数应归属的空间类型。
    优先使用 Violation 自身指定的 space_type，否则尝试从关联的 Booking 中获取。
    为了提高效率，并避免在同一个实例生命周期内重复查询，可以缓存结果。
    """
    # 使用一个独特的属性名来缓存，避免与其他可能存在的_cached_space_type冲突
    if hasattr(violation_instance, '_cached_space_type_for_penalty_calc'):
        return getattr(violation_instance, '_cached_space_type_for_penalty_calc')

    target_space_type = None
    if violation_instance.space_type:
        target_space_type = violation_instance.space_type
    elif violation_instance.booking_id:
        try:
            # 使用 select_related 避免 N+1 查询
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
                f"Booking {violation_instance.booking_id} not found for violation {violation_instance.pk if violation_instance.pk else 'new'}, cannot infer space type for penalty points.")
            pass
        except Exception as e:
            logger.error(
                f"Error inferring space type from booking {violation_instance.booking_id} for violation {violation_instance.pk if violation_instance.pk else 'new'}: {e}")

    # 缓存结果
    setattr(violation_instance, '_cached_space_type_for_penalty_calc', target_space_type)
    return target_space_type

def _recalculate_user_penalty_points(user: CustomUser, space_type: Optional[SpaceType]) -> int:
    """
    私有辅助函数：重新计算用户在特定空间类型下所有未解决的违规点数总和。
    """
    # 构建查询条件，确保正确处理 None (表示全局) 的情况
    q_conditions = Q(user=user, is_resolved=False)
    if space_type:
        q_conditions &= Q(space_type=space_type)
    else:
        q_conditions &= Q(space_type__isnull=True)

    total_active_points = Violation.objects.filter(q_conditions).aggregate(total=models.Sum('penalty_points'))[
                              'total'] or 0

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

    existing_active_ban_query = UserSpaceTypeBan.objects.select_related('ban_policy_applied').filter(
        user=ban_user,
        space_type=ban_space_type,
        end_date__gt=timezone.now()
    )
    existing_active_ban = existing_active_ban_query.first()

    applicable_policies_query = SpaceTypeBanPolicy.objects.filter(
        Q(space_type=ban_space_type) | Q(space_type__isnull=True),
        is_active=True,
        threshold_points__lte=current_points
    ).order_by('-priority', '-threshold_points')

    policy_to_apply = applicable_policies_query.first()

    ban_start = timezone.now()

    if policy_to_apply:
        new_ban_end = ban_start + policy_to_apply.ban_duration
        reason_template = f"因在 {space_type_name} 累计 {current_points} 点 (触发策略: {policy_to_apply.threshold_points}点/{policy_to_apply.ban_duration}) 触发禁用"

        if existing_active_ban:
            should_update_existing_ban = False
            update_fields = set()
            action_description = ""

            old_policy = existing_active_ban.ban_policy_applied

            is_new_policy_stricter = False
            if old_policy:
                if policy_to_apply.priority > old_policy.priority:
                    is_new_policy_stricter = True
                elif policy_to_apply.priority == old_policy.priority and policy_to_apply.threshold_points > old_policy.threshold_points:
                    is_new_policy_stricter = True
                # 新旧策略完全不同，即使不更严格也需要更新关联策略和原因
                elif policy_to_apply.pk != old_policy.pk:
                    is_new_policy_stricter = True  # Treat as stricter if policy changes even if numeric values are same for simplicity in this path

            else:
                is_new_policy_stricter = True

            # 检查是否需要更新现有禁令（延长、加强、或重新激活）
            if new_ban_end > existing_active_ban.end_date or \
                    is_new_policy_stricter or \
                    existing_active_ban.end_date <= timezone.now():  # 重新激活过期禁令

                existing_active_ban.start_date = ban_start
                existing_active_ban.end_date = new_ban_end
                existing_active_ban.ban_policy_applied = policy_to_apply
                existing_active_ban.reason = reason_template + " - 更新或延长禁用"
                update_fields.update(['start_date', 'end_date', 'ban_policy_applied', 'reason'])
                should_update_existing_ban = True
                action_description = "更新/延长/重新激活禁用"
            elif new_ban_end < existing_active_ban.end_date:
                # 策略降级，禁用时间缩短
                existing_active_ban.end_date = new_ban_end
                existing_active_ban.reason = reason_template + " - 调整缩短禁用"
                update_fields.add('end_date')
                update_fields.add('reason')
                should_update_existing_ban = True
                action_description = "调整缩短禁用"
            else:
                # 禁令保持不变，可能只是 updated_at 刷新
                pass

            if should_update_existing_ban:
                # NEW: 避免 UserSpaceTypeBan 的 post_save 信号再次触发 _apply_ban_policy
                # 虽然 UserSpaceTypeBan 的 post_save 信号只触发 ban_cache_invalidation_task
                # 但这里依然可以加一个防御性措施，或者更重要的是确保其自身不会再次触发 penalty_points_record.save
                existing_active_ban.save(update_fields=list(update_fields))
                logger.info(
                    f"Ban ID {existing_active_ban.pk} for user {ban_user.id} in {space_type_name} {action_description} until {existing_active_ban.end_date.strftime('%Y-%m-%d %H:%M')}.")
            else:
                logger.debug(
                    f"Existing ban for user {ban_user.id} in {space_type_name}(ID:{ban_space_type.pk if ban_space_type else None}) is already current or more strict, no update needed.")

        else:
            UserSpaceTypeBan.objects.create(
                user=ban_user,
                space_type=ban_space_type,
                start_date=ban_start,
                end_date=new_ban_end,
                ban_policy_applied=policy_to_apply,
                reason=reason_template + " - 新建禁用",
                issued_by=None
            )
            logger.info(
                f"New ban created for user {ban_user.id} in {space_type_name}(ID:{ban_space_type.pk if ban_space_type else None}) until {new_ban_end.strftime('%Y-%m-%d %H:%M')}.")

        # NEW: 确保 penalty_points_record.last_ban_trigger_at 的更新不引起无限递归
        # 仅当实际需要更新 last_ban_trigger_at 且其值真的改变时才调用 save
        if penalty_points_record.last_ban_trigger_at != ban_start:
            # 临时断开 UserPenaltyPointsPerSpaceType 的 post_save 信号
            post_save.disconnect(user_penalty_points_post_save_handler, sender=UserPenaltyPointsPerSpaceType)
            try:
                penalty_points_record.last_ban_trigger_at = ban_start
                penalty_points_record.save(update_fields=['last_ban_trigger_at', 'updated_at'])
            finally:
                # 重新连接信号
                post_save.disconnect(user_penalty_points_post_save_handler, sender=UserPenaltyPointsPerSpaceType)
                post_save.connect(user_penalty_points_post_save_handler, sender=UserPenaltyPointsPerSpaceType)

    else:
        if existing_active_ban:
            original_end_date = existing_active_ban.end_date
            if existing_active_ban.end_date > timezone.now():
                # NEW: 临时断开 UserSpaceTypeBan 的 post_save 信号
                post_save.disconnect(user_ban_post_save_handler, sender=UserSpaceTypeBan)
                try:
                    existing_active_ban.end_date = timezone.now()
                    existing_active_ban.reason += f" (自动解除: 点数降至 {current_points}，低于所有禁用策略阈值)"
                    existing_active_ban.save(update_fields=['end_date', 'reason'])
                finally:
                    # 重新连接信号
                    post_save.disconnect(user_ban_post_save_handler, sender=UserSpaceTypeBan)
                    post_save.connect(user_ban_post_save_handler, sender=UserSpaceTypeBan)

                logger.info(
                    f"Ban ID {existing_active_ban.pk} for user {ban_user.id} in {space_type_name} automatically lifted. Was set until {original_end_date.strftime('%Y-%m-%d %H:%M')}.")
            else:
                logger.debug(
                    f"Ban ID {existing_active_ban.pk} for user {ban_user.id} in {space_type_name} is already expired, no action needed.")
        else:
            logger.debug(
                f"No applicable ban policy and no existing active ban for user {ban_user.id} in {space_type_name}. No ban operations needed.")

# --- Booking 模型的信号处理 (保持不变) ---
@receiver(pre_save, sender=Booking)
def store_old_booking_data_on_pre_save(sender, instance, **kwargs):
    if instance.pk:  # 仅对已存在的对象有效
        try:
            old_instance = sender.objects.select_related('space', 'bookable_amenity').get(pk=instance.pk)
            # 存储关键的旧属性
            instance._old_status = old_instance.status
            instance._old_start_time = old_instance.start_time
            instance._old_end_time = old_instance.end_time
            instance._old_space_id = old_instance.space_id
            instance._old_bookable_amenity_id = old_instance.bookable_amenity_id
            instance._old_related_space_id = old_instance.related_space_id
            # Bug fix: `_old_related_space` might be needed for deletion or change detection
            instance._old_related_space = old_instance.related_space
            logger.debug(f"Pre-save: Stored old data for Booking ID {instance.pk}")
        except sender.DoesNotExist:
            logger.warning(f"Booking with PK {instance.pk} not found during pre_save. Treating as new instance.")
            # 对于新实例，或因并发操作删除/创建，初始化旧属性为 None/default
            instance._old_status = None
            instance._old_start_time = None
            instance._old_end_time = None
            instance._old_space_id = None
            instance._old_bookable_amenity_id = None
            instance._old_related_space_id = None
            instance._old_related_space = None
    else:
        # 新实例，不需要存储旧数据，但可以初始化属性以避免 AttributeError
        instance._old_status = None
        instance._old_start_time = None
        instance._old_end_time = None
        instance._old_space_id = None
        instance._old_bookable_amenity_id = None
        instance._old_related_space_id = None
        instance._old_related_space = None

@receiver(post_save, sender=Booking)
def booking_post_save_handler(sender, instance, created, **kwargs):
    logger.debug(f"Post_save signal for Booking (ID:{instance.pk}), created: {created}")

    # 获取 pre_save 存储的旧数据
    old_status = getattr(instance, '_old_status', None)
    old_start_time = getattr(instance, '_old_start_time', None)
    old_end_time = getattr(instance, '_old_end_time', None)
    old_space_id = getattr(instance, '_old_space_id', None)
    old_bookable_amenity_id = getattr(instance, '_old_bookable_amenity_id', None)
    old_related_space_id = getattr(instance, '_old_related_space_id', None)  # 确保获取到

    # 判断是否需要广泛的缓存失效 (例如，影响列表或空间可用性)
    needs_broad_invalidation = created

    if not created:
        # 状态或时间变化会导致列表和资源可用性变化
        if old_status != instance.status or \
                old_start_time != instance.start_time or \
                old_end_time != instance.end_time:
            needs_broad_invalidation = True
            logger.debug(
                f"Booking {instance.pk} status or time changed. old_status={old_status}, new_status={instance.status}, old_time={old_start_time}-{old_end_time}, new_time={instance.start_time}-{instance.end_time}")

        # 预订目标或关联空间发生变化
        if old_space_id != instance.space_id or \
                old_bookable_amenity_id != instance.bookable_amenity_id or \
                old_related_space_id != instance.related_space_id:
            needs_broad_invalidation = True
            logger.debug(f"Booking {instance.pk} target resource or related space changed.")
            # If related space changes, invalidate caches for both old and new related spaces
            if old_related_space_id and old_related_space_id != instance.related_space_id:
                booking_cache_invalidation_task.delay(instance.pk, old_related_space_id=old_related_space_id,
                                                      current_related_space_id=instance.related_space_id,
                                                      needs_broad_invalidation=needs_broad_invalidation)
                return  # Early exit as task handles everything

    # 异步触发缓存失效任务
    # 将需要的信息传递给 Celery 任务，让其执行具体的缓存清除逻辑
    booking_cache_invalidation_task.delay(instance.pk,
                                          old_related_space_id=old_related_space_id,
                                          current_related_space_id=instance.related_space_id,
                                          needs_broad_invalidation=needs_broad_invalidation)
    logger.info(f"Booking (ID:{instance.pk}) post_save: Dispatched cache invalidation task.")

@receiver(post_delete, sender=Booking)
def booking_post_delete_handler(sender, instance, **kwargs):
    logger.debug(f"Post_delete signal for Booking (ID:{instance.pk})")

    # 获取 post_save 存储的旧数据 (这里可能无法直接访问 pre_delete 存储的数据，但 `instance` 仍然有其 ID 和关联信息)
    # 对于删除，我们特别关注其曾经关联的资源
    related_space_id_on_delete = instance.related_space_id

    # 异步触发缓存失效任务
    # 对于删除，`needs_broad_invalidation` 总是 True，因为它改变了资源占用情况
    booking_cache_invalidation_task.delay(instance.pk,
                                          is_deleted_event=True,
                                          old_related_space_id=related_space_id_on_delete,
                                          # 删除事件中，其related_space就是旧的related_space
                                          needs_broad_invalidation=True)
    logger.info(f"Booking (ID:{instance.pk}) post_delete: Dispatched cache invalidation task.")

# --- UserSpaceTypeBan 模型的信号处理 (NEW) ---

@receiver(pre_save, sender=UserSpaceTypeBan)
def user_ban_pre_save_handler(sender, instance, **kwargs):
    """
    在 UserSpaceTypeBan 实例保存前，存储被修改字段的旧值 (user, space_type, end_date)。
    这对于 post_save 中比较字段变化和正确失效缓存至关重要。
    """
    if instance.pk:  # 仅针对已存在的实例进行更新时有效
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            # 存储旧的用户ID和空间类型ID，以及end_date用于判断禁令是否解除
            instance._old_user_pk = old_instance.user.pk
            instance._old_space_type_pk = old_instance.space_type.pk if old_instance.space_type else None
            instance._old_end_date = old_instance.end_date  # 用于判断禁用解除
            logger.debug(
                f"UserSpaceTypeBan pre_save for ID {instance.pk}: Stored old user_pk={instance._old_user_pk}, old_space_type_pk={instance._old_space_type_pk}, old_end_date={instance._old_end_date}")
        except sender.DoesNotExist:
            logger.warning(
                f"UserSpaceTypeBan with PK {instance.pk} not found in pre_save; treating as new instance for old values.")
            instance._old_user_pk = None
            instance._old_space_type_pk = None
            instance._old_end_date = None
    else:  # 新实例
        instance._old_user_pk = None
        instance._old_space_type_pk = None
        instance._old_end_date = None

@receiver(post_save, sender=UserSpaceTypeBan)
def user_ban_post_save_handler(sender, instance, created, **kwargs):
    """
    当 UserSpaceTypeBan 实例保存 (创建或更新) 后，异步触发缓存失效任务。
    根据 user 和 space_type 的变化，失效当前和旧状态相关的缓存。
    """
    logger.info(f"UserSpaceTypeBan post_save signal received for ID: {instance.pk}, created: {created}.")

    current_user_pk = instance.user.pk
    current_space_type_pk = instance.space_type.pk if instance.space_type else None

    # 从 pre_save 中获取旧值
    old_user_pk = getattr(instance, '_old_user_pk', None)
    old_space_type_pk = getattr(instance, '_old_space_type_pk', None)
    # old_end_date = getattr(instance, '_old_end_date', None) # 可以在任务中判断是否失效

    # 调度 Celery 任务进行缓存失效，传递所有必要信息
    # 任务会判断 user_pk/old_user_pk 和 affected_space_type_pk/old_space_type_pk 的变化来决定如何清除缓存
    ban_cache_invalidation_task.delay(
        user_pk=current_user_pk,
        affected_space_type_pk=current_space_type_pk,
        old_user_pk=old_user_pk,
        old_space_type_pk=old_space_type_pk
    )
    logger.info(f"UserSpaceTypeBan (ID:{instance.pk}) post_save: Dispatched ban cache invalidation task.")

@receiver(post_delete, sender=UserSpaceTypeBan)
def user_ban_post_delete_handler(sender, instance, **kwargs):
    """
    当 UserSpaceTypeBan 实例删除后，异步触发缓存失效任务。
    主要需要失效被删除禁令所影响的缓存键。
    """
    logger.info(f"UserSpaceTypeBan post_delete signal received for ID: {instance.pk}.")

    # 在删除时，instance 仍然包含被删除对象的数据
    deleted_user_pk = instance.user.pk
    deleted_space_type_pk = instance.space_type.pk if instance.space_type else None

    # 调度 Celery 任务进行缓存失效。
    # 这里只传递被删除禁令本身的信息，任务会将其视为“旧状态”来进行失效。
    ban_cache_invalidation_task.delay(
        user_pk=deleted_user_pk,  # 这里的user_pk就是受影响的用户
        affected_space_type_pk=deleted_space_type_pk,  # 这里的affected_space_type_pk就是受影响的空间类型
        old_user_pk=None,  # 删除操作没有“旧用户”，只有被删除禁令的用户
        old_space_type_pk=None  # 删除操作没有“旧空间类型”，只有被删除禁令的空间类型
    )
    logger.info(f"UserSpaceTypeBan (ID:{instance.pk}) post_delete: Dispatched ban cache invalidation task.")

# --- UserSpaceTypeExemption 模型的信号处理 (NEW) ---

@receiver(pre_save, sender=UserSpaceTypeExemption)
def user_exemption_pre_save_handler(sender, instance, **kwargs):
    """
    在 UserSpaceTypeExemption 实例保存前，存储被修改字段的旧值 (user, space_type, end_date)。
    这对于 post_save 中比较字段变化和正确失效缓存至关重要。
    """
    if instance.pk:  # 仅针对已存在的实例进行更新时有效
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            # 存储旧的用户ID和空间类型ID，以及end_date用于判断豁免是否解除
            instance._old_user_pk = old_instance.user.pk
            instance._old_space_type_pk = old_instance.space_type.pk if old_instance.space_type else None
            instance._old_end_date = old_instance.end_date  # 用于判断豁免是否到期
            logger.debug(
                f"UserSpaceTypeExemption pre_save for ID {instance.pk}: Stored old user_pk={instance._old_user_pk}, old_space_type_pk={instance._old_space_type_pk}, old_end_date={instance._old_end_date}")
        except sender.DoesNotExist:
            logger.warning(
                f"UserSpaceTypeExemption with PK {instance.pk} not found in pre_save; treating as new instance for old values.")
            instance._old_user_pk = None
            instance._old_space_type_pk = None
            instance._old_end_date = None
    else:  # 新实例
        instance._old_user_pk = None
        instance._old_space_type_pk = None
        instance._old_end_date = None

@receiver(post_save, sender=UserSpaceTypeExemption)
def user_exemption_post_save_handler(sender, instance, created, **kwargs):
    """
    当 UserSpaceTypeExemption 实例保存 (创建或更新) 后，异步触发缓存失效任务。
    根据 user 和 space_type 的变化，失效当前和旧状态相关的缓存。
    """
    logger.info(f"UserSpaceTypeExemption post_save signal received for ID: {instance.pk}, created: {created}.")

    current_user_pk = instance.user.pk
    current_space_type_pk = instance.space_type.pk if instance.space_type else None

    # 从 pre_save 中获取旧值
    old_user_pk = getattr(instance, '_old_user_pk', None)
    old_space_type_pk = getattr(instance, '_old_space_type_pk', None)

    # 调度 Celery 任务进行缓存失效，传递所有必要信息
    exemption_cache_invalidation_task.delay(
        user_pk=current_user_pk,
        affected_space_type_pk=current_space_type_pk,
        old_user_pk=old_user_pk,
        old_space_type_pk=old_space_type_pk
    )
    logger.info(f"UserSpaceTypeExemption (ID:{instance.pk}) post_save: Dispatched exemption cache invalidation task.")

@receiver(post_delete, sender=UserSpaceTypeExemption)
def user_exemption_post_delete_handler(sender, instance, **kwargs):
    """
    当 UserSpaceTypeExemption 实例删除后，异步触发缓存失效任务。
    主要需要失效被删除豁免所影响的缓存键。
    """
    logger.info(f"UserSpaceTypeExemption post_delete signal received for ID: {instance.pk}.")

    # 在删除时，instance 仍然包含被删除对象的数据
    deleted_user_pk = instance.user.pk
    deleted_space_type_pk = instance.space_type.pk if instance.space_type else None

    # 调度 Celery 任务进行缓存失效。
    exemption_cache_invalidation_task.delay(
        user_pk=deleted_user_pk,
        affected_space_type_pk=deleted_space_type_pk,
        old_user_pk=None,  # 删除操作没有“旧用户”，只有被删除豁免的用户
        old_space_type_pk=None  # 删除操作没有“旧空间类型”，只有被删除豁免的用户
    )
    logger.info(f"UserSpaceTypeExemption (ID:{instance.pk}) post_delete: Dispatched exemption cache invalidation task.")

# --- Violation 模型的信号处理 (NEW AND CRUCIAL) ---

@receiver(pre_save, sender=Violation)
def violation_pre_save_handler(sender, instance, **kwargs):
    """
    在 Violation 对象保存前，存储其旧状态，特别是 is_resolved 和 penalty_points，
    以及其关联的空间类型，以便 post_save 能够正确地重新计算违约点数和评估禁用策略。
    """
    if instance.pk:  # 仅对已存在的对象有效
        try:
            # 重新从数据库获取一次旧实例，确保 _old_cached_space_type_for_penalty_calc 是通过真实数据获取的
            old_instance = sender.objects.select_related('space_type', 'booking__space__space_type',
                                                         'booking__bookable_amenity__space__space_type').get(
                pk=instance.pk)
            instance._old_is_resolved = old_instance.is_resolved
            instance._old_penalty_points = old_instance.penalty_points
            instance._old_cached_space_type_for_penalty_calc = _get_violation_target_space_type(
                old_instance)  # 调用辅助函数获取旧的空间类型
            logger.debug(
                f"Violation pre_save for ID {instance.pk}: Stored old is_resolved={instance._old_is_resolved}, old_penalty_points={instance._old_penalty_points}, old_space_type={instance._old_cached_space_type_for_penalty_calc.pk if instance._old_cached_space_type_for_penalty_calc else 'None'}")
        except sender.DoesNotExist:
            logger.warning(f"Violation with PK {instance.pk} not found during pre_save. Treating as new.")
            instance._old_is_resolved = False  # Default for new
            instance._old_penalty_points = 0  # Default for new
            instance._old_cached_space_type_for_penalty_calc = None
    else:  # 新实例
        instance._old_is_resolved = False  # Default for new
        instance._old_penalty_points = 0  # Default for new
        instance._old_cached_space_type_for_penalty_calc = None

@receiver(post_save, sender=Violation)
def violation_post_save_handler(sender, instance, created, **kwargs):
    """
    当 Violation 实例保存 (创建或更新) 后，重新计算用户违约点数并评估禁用策略。
    此处理器会考虑豁免的状态改变，点数改变以及关联空间类型改变。
    """
    logger.info(f"Violation post_save signal received for ID: {instance.pk}, created: {created}.")

    if not instance.user:
        logger.warning(f"Violation {instance.pk} has no associated user, skipping penalty points update.")
        return

    current_target_space_type = _get_violation_target_space_type(instance)
    old_target_space_type = getattr(instance, '_old_cached_space_type_for_penalty_calc', None)

    # 收集所有受影响的空间类型（当前和旧的），以便重新计算
    # 确保 None 也被视为一个需要处理的“空间类型”维度（代表全局）
    affected_space_types: Set[Optional[SpaceType]] = set()
    if current_target_space_type: affected_space_types.add(current_target_space_type)
    if old_target_space_type: affected_space_types.add(old_target_space_type)
    # 如果当前或旧的都不是特定空间类型，那么全局维度可能被影响到
    if current_target_space_type is None or old_target_space_type is None:
        affected_space_types.add(None)

    # 仅当以下任何条件发生时才需要重新评估点数和禁用策略：
    # 1. 违规记录是新创建的
    # 2. 解决状态改变了 (is_resolved)
    # 3. 违约点数改变了 (penalty_points)
    # 4. 关联的空间类型改变了 (这会影响哪个 UserPenaltyPointsPerSpaceType 记录)
    points_changed = instance.penalty_points != getattr(instance, '_old_penalty_points', 0)
    resolved_changed = instance.is_resolved != getattr(instance, '_old_is_resolved', False)
    space_type_changed = (current_target_space_type != old_target_space_type)

    if created or points_changed or resolved_changed or space_type_changed:
        logger.debug(
            f"Violation {instance.pk} triggered re-evaluation. Created: {created}, Points changed: {points_changed}, Resolved changed: {resolved_changed}, Space type changed: {space_type_changed}")
        for st in affected_space_types:
            try:
                current_total_active_points = _recalculate_user_penalty_points(instance.user, st)

                penalty_points_record, created_pp = UserPenaltyPointsPerSpaceType.objects.get_or_create(
                    user=instance.user,
                    space_type=st  # 这里的 st 可能是 SpaceType 实例或 None
                )

                # --- 关键修改：强制更新 updated_at，以确保 UserPenaltyPointsPerSpaceType 的 post_save 总是触发 ---
                # 即使 current_penalty_points 没有变化，但如果 Violation 的状态改变了，
                # 也应该刷新 UserPenaltyPointsPerSpaceType 的 updated_at，从而触发其 post_save 重新评估禁用策略。
                needs_explicit_save_on_penalty_record = False
                if penalty_points_record.current_penalty_points != current_total_active_points:
                    penalty_points_record.current_penalty_points = current_total_active_points
                    penalty_points_record.last_violation_at = timezone.now()
                    needs_explicit_save_on_penalty_record = True
                elif created or points_changed or resolved_changed or space_type_changed:
                    # 如果 Violation 任何相关状态改变，即使总点数没变，也要更新 penalty_points_record 的 updated_at
                    # 确保 post_save 再次触发 _apply_ban_policy
                    # 例如：解决了某个违规，但又有新的违规，总点数不变
                    # 或者只有描述改变，但为了健壮性，这里也触发一次。
                    needs_explicit_save_on_penalty_record = True
                    penalty_points_record.last_violation_at = timezone.now() # 更新最后违规时间

                if needs_explicit_save_on_penalty_record:
                    penalty_points_record.save(update_fields=['current_penalty_points', 'last_violation_at', 'updated_at'])
                    logger.info(
                        f"UserPenaltyPointsPerSpaceType for user {instance.user.id} in space type {st.id if st else 'Global'} explicit save triggered due to Violation {instance.pk} change.")
                else:
                    logger.debug(
                        f"UserPenaltyPointsPerSpaceType for user {instance.user.id} in space type {st.id if st else 'Global'} not explicitly saved as no relevant changes, but its post_save would trigger re-evaluation if updated by other means.")

            except Exception as e:
                logger.error(
                    f"Error processing penalty points for user {instance.user.id} in space_type {st.id if st else 'Global'} after Violation {instance.pk} save: {e}",
                    exc_info=True)
    else:
        logger.debug(
            f"Violation {instance.pk} saved but no relevant fields changed, skipping penalty points re-evaluation.")

@receiver(post_delete, sender=Violation)
def violation_post_delete_handler(sender, instance, **kwargs):
    """
    当 Violation 实例删除后，重新计算用户活跃违约点数并评估禁用策略。
    """
    logger.info(f"Violation post_delete signal received for ID: {instance.pk}.")

    if not instance.user:
        logger.warning(
            f"Violation {instance.pk} deleted has no associated user, skipping penalty points update.")
        return

    # 获取被删除违规的关联空间类型
    target_space_type_was = _get_violation_target_space_type(instance)

    # 确保无论 target_space_type 是什么 (包括 None)，都进行处理
    affected_space_types: Set[Optional[SpaceType]] = set()
    if target_space_type_was: affected_space_types.add(target_space_type_was)
    affected_space_types.add(None)  # Always include None for global check

    for st in affected_space_types:
        try:
            # 重新计算该用户在该空间类型下的活跃点数
            current_total_active_points = _recalculate_user_penalty_points(instance.user, st)

            penalty_points_record = UserPenaltyPointsPerSpaceType.objects.filter(
                user=instance.user,
                space_type=st  # 这里的 st 可能是 SpaceType 实例或 None
            ).first()

            needs_explicit_save_on_penalty_record = False
            if penalty_points_record:
                if penalty_points_record.current_penalty_points != current_total_active_points:
                    penalty_points_record.current_penalty_points = current_total_active_points
                    penalty_points_record.last_violation_at = timezone.now()
                    needs_explicit_save_on_penalty_record = True
                    logger.info(
                        f"User {instance.user.id} penalty points changed from {penalty_points_record.current_penalty_points} to {current_total_active_points} after deleting violation {instance.pk} in space type {st.id if st else 'Global'}.")
                # else: # 如果点数没变，但删除了违规，也要刷新 updated_at 触发重新评估禁用
                #     needs_explicit_save_on_penalty_record = True # 确保每次删除都能触发更新

                if needs_explicit_save_on_penalty_record:
                    penalty_points_record.save(
                        update_fields=['current_penalty_points', 'last_violation_at',
                                       'updated_at'])  # 更新点数并触发保存，信号会再次触发
                else:
                    # 如果点数没变，也要确保 _apply_ban_policy 被调用
                    # 因为删除一个违规可能导致虽然总点数没变，但之前的点数构成方式变了，或者策略本身变了
                    # 现在的设计让 UserPenaltyPointsPerSpaceType 的 post_save 信号直接调用 _apply_ban_policy
                    # 所以如果这里没有 save，那么就需要确保在删除之后也能触发一次重新评估。
                    # 如果 `penalty_points_record` 确实存在且没有 `save` 发生，应该手动调用 `_apply_ban_policy`
                    _apply_ban_policy(penalty_points_record)

            else: # 如果没有找到 penalty_points_record
                logger.debug(
                    f"No UserPenaltyPointsPerSpaceType record found for user {instance.user.id} in space type {st.id if st else 'Global'} after deleting violation {instance.pk}. Ensuring ban policy re-evaluation.")
                # 即使没有 penalty_points_record，也可能存在活跃禁令需要解除。
                # 创建一个临时的 record，将其点数设置为当前计算的点数 (很可能是0)，然后调用 _apply_ban_policy
                temp_penalty_record = UserPenaltyPointsPerSpaceType(user=instance.user, space_type=st,
                                                                    current_penalty_points=current_total_active_points)
                _apply_ban_policy(temp_penalty_record)

        except Exception as e:
            logger.error(
                f"Error processing penalty points for user {instance.user.id} in space_type {st.id if st else 'Global'} after Violation {instance.pk} delete: {e}",
                exc_info=True)

# NEW: 为 UserPenaltyPointsPerSpaceType 模型添加 post_save 信号
@receiver(post_save, sender=UserPenaltyPointsPerSpaceType)
def user_penalty_points_post_save_handler(sender, instance, created, **kwargs):
    """
    当 UserPenaltyPointsPerSpaceType 实例被创建或更新后，
    重新评估并应用禁用策略，即使其 current_penalty_points 未改变，
    以处理策略自身变化或现有禁用过期等情况。
    """
    logger.info(f"UserPenaltyPointsPerSpaceType post_save signal received for ID: {instance.pk}, created: {created}.")
    # 直接调用 _apply_ban_policy 确保禁用策略被重新评估
    _apply_ban_policy(instance)
    logger.debug(f"Ban policy re-applied for UserPenaltyPointsPerSpaceType ID: {instance.pk}.")