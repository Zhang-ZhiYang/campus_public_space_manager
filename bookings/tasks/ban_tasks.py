# bookings/tasks/ban_tasks.py
import logging
from celery import shared_task
from typing import Optional

from core.service.factory import ServiceFactory

# from core.service.cache import CacheService # CacheService will be accessed via UserBanService

logger = logging.getLogger(__name__)

# 使用全局变量缓存 Service 实例，避免在每次信号触发时重复创建
_user_ban_service_instance = None


def get_user_ban_service_instance():
    """惰性加载 UserBanService 实例。"""
    global _user_ban_service_instance
    if _user_ban_service_instance is None:
        _user_ban_service_instance = ServiceFactory.get_service('UserBanService')
    return _user_ban_service_instance


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def ban_cache_invalidation_task(self,
                                user_pk: int,
                                affected_space_type_pk: Optional[int] = None,
                                old_user_pk: Optional[int] = None,
                                old_space_type_pk: Optional[int] = None):
    """
    Celery 任务：异步处理 UserSpaceTypeBan 模型变化后的缓存失效。
    它会调用 UserBanService 中的缓存失效逻辑。

    :param self: Celery Task 实例自身。
    :param user_pk: 当前 (新) 禁令相关用户的 PK。
    :param affected_space_type_pk: 当前 (新) 禁令相关空间类型的 PK (None 表示全局)。
    :param old_user_pk: 旧禁令相关用户的 PK (仅当用户发生变化时有用，否则为 None)。
    :param old_space_type_pk: 旧禁令相关空间类型的 PK (仅当空间类型发生变化时有用，否则为 None)。
    """
    logger.info(
        f"Ban Cache Invalidation Task (ID:{self.request.id}) started for user_pk={user_pk}, "
        f"affected_space_type_pk={affected_space_type_pk}, old_user_pk={old_user_pk}, old_space_type_pk={old_space_type_pk}"
    )

    user_ban_service = get_user_ban_service_instance()

    try:
        # 失效当前（新）状态对应的缓存
        user_ban_service.invalidate_user_ban_cache(user_pk=user_pk, affected_space_type_pk=affected_space_type_pk)

        # 如果是更新操作中用户或空间类型发生了变化，则失效旧状态对应的缓存
        # 1. 用户变更：失效旧用户在原空间类型下的缓存
        if old_user_pk is not None and old_user_pk != user_pk:
            user_ban_service.invalidate_user_ban_cache(user_pk=old_user_pk, affected_space_type_pk=old_space_type_pk)
            logger.debug(f"Invalidated ban cache for old user {old_user_pk} (old space type {old_space_type_pk}).")

        # 2. 空间类型变更：失效原用户在旧空间类型下的缓存
        if old_space_type_pk is not None and old_space_type_pk != affected_space_type_pk:
            # 此处 `user_pk` 仍然是 `current_user_pk`，因为是针对该用户的禁用类型发生了变化。
            user_ban_service.invalidate_user_ban_cache(user_pk=user_pk, affected_space_type_pk=old_space_type_pk)
            logger.debug(f"Invalidated ban cache for user {user_pk} (old space type {old_space_type_pk}).")

        # 特殊情况：如果旧禁令是全局禁令 (old_space_type_pk is None)，并且它被修改了
        # （例如，从全局禁用变为特定空间类型的禁用，或者被删除了），
        # 那么所有特定空间类型的缓存都需要失效。
        # 注意：这里的逻辑需要确保与 `invalidate_user_ban_cache` 的内部处理保持一致，
        # `invalidate_user_ban_cache(user_pk, None)` 就会处理这种全局失效。
        if old_space_type_pk is None and affected_space_type_pk is not None:
            logger.info(f"原禁令为全局禁令。重新失效用户 {user_pk} 的所有特定空间类型缓存。")
            user_ban_service.invalidate_user_ban_cache(user_pk=user_pk, affected_space_type_pk=None)

        logger.info(f"Ban Cache Invalidation Task (ID:{self.request.id}) completed.")

    except Exception as e:
        logger.exception(
            f"Error in ban_cache_invalidation_task for user_pk {user_pk}, affected_space_type_pk {affected_space_type_pk}. Retrying...")
        self.retry(exc=e)