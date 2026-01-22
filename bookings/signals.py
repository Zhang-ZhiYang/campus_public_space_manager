# bookings/signals.py
import logging
from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.utils import timezone  # 导入 timezone

from bookings.models import Booking, Space, UserSpaceTypeBan, \
    UserSpaceTypeExemption  # 从 .models 导入 Booking 和 Space 用于类型提示
from core.service.cache import CacheService  # 假设 CacheService 位于 core.service.cache
from bookings.tasks.booking_tasks import booking_cache_invalidation_task  # 导入 Celery 任务
from bookings.tasks.ban_tasks import ban_cache_invalidation_task
from bookings.tasks.exemption_tasks import exemption_cache_invalidation_task

logger = logging.getLogger(__name__)


# --- Booking 模型的信号处理 ---

@receiver(pre_save, sender=Booking)
def store_old_booking_data_on_pre_save(sender, instance, **kwargs):
    """
    在 Booking 对象保存前，存储其旧状态，以便 post_save 信号可以检测到哪些字段发生了变化，
    特别是影响列表或资源可用性的字段（如状态、时间、关联资源）。
    """
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
    """
    当 Booking 对象被保存（创建或更新）时，异步触发相关缓存的失效。
    确保无论是通过 Admin 还是其它直接方法修改 Booking，缓存都能同步更新。
    """
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
    """
    当 Booking 对象被删除时，异步触发相关缓存的失效。
    """
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
        old_space_type_pk=None  # 删除操作没有“旧空间类型”，只有被删除豁免的空间类型
    )
    logger.info(f"UserSpaceTypeExemption (ID:{instance.pk}) post_delete: Dispatched exemption cache invalidation task.")