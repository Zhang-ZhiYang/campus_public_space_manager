# bookings/signals.py (UPDATED - Removed emojis from logs)
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from bookings.models import Violation  # 只从 .models 导入需要监听的模型
import logging  # 导入 logging 模块

# 获取该模块的 logger 实例
logger = logging.getLogger(__name__)

# 从 services.violation_service 导入业务逻辑函数
# 注意：这里是 bookings.service.violation_service 目录和文件结构
from bookings.service.violation_service import (
    _get_violation_target_space_type,  # 辅助函数也需要导入
    handle_violation_save,
    handle_violation_delete
)

@receiver(pre_save, sender=Violation)
def store_old_violation_attrs(sender, instance, **kwargs):
    """
    在保存前存储旧的 is_resolved, penalty_points 和 space_type 状态，
    以便 post_save 的业务逻辑能够判断其是否改变，并进行精确的点数更新。
    """
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            instance._old_is_resolved = old_instance.is_resolved
            instance._old_penalty_points = old_instance.penalty_points
            instance._old_cached_space_type = _get_violation_target_space_type(old_instance)
            logger.debug(
                f"[pre_save] Violation {instance.pk}: Storing old attributes - resolved={old_instance.is_resolved}, points={old_instance.penalty_points}, space_type={instance._old_cached_space_type}")
        except sender.DoesNotExist:
            logger.warning(
                f"[pre_save] Violation {instance.pk} not found during pre_save. Treating as new or possibly race condition deleted.")
            instance._old_is_resolved = False
            instance._old_penalty_points = 0
            instance._old_cached_space_type = None
    else:
        instance._old_is_resolved = False
        instance._old_penalty_points = 0
        instance._old_cached_space_type = None
        logger.debug(f"[pre_save] New Violation: Initializing old attributes for new instance.")

@receiver(post_save, sender=Violation)
def violation_post_save_handler(sender, instance, created, **kwargs):
    """
    Violation 实例保存后的处理。调用 services 层逻辑。
    """
    old_is_resolved = getattr(instance, '_old_is_resolved', False)
    old_penalty_points = getattr(instance, '_old_penalty_points', 0)
    old_cached_space_type = getattr(instance, '_old_cached_space_type', None)

    action = "created" if created else "updated"
    logger.info(
        f"[post_save] Violation {instance.pk} {action}. Calling handle_violation_save. Old: resolved={old_is_resolved}, points={old_penalty_points}, space_type={old_cached_space_type}.")

    handle_violation_save(
        instance,
        created,
        old_is_resolved,
        old_penalty_points,
        old_cached_space_type
    )
    logger.debug(f"[post_save] handle_violation_save for Violation {instance.pk} completed.")

@receiver(post_delete, sender=Violation)
def violation_post_delete_handler(sender, instance, **kwargs):
    """
    Violation 实例删除后的处理。调用 services 层逻辑。
    """
    logger.info(
        f"[post_delete] Violation {instance.pk} deleted. Calling handle_violation_delete. Points: {instance.penalty_points}, Space Type inferred: {_get_violation_target_space_type(instance)}.")
    handle_violation_delete(instance)
    logger.debug(f"[post_delete] handle_violation_delete for Violation {instance.pk} completed.")