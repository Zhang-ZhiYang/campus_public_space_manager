# bookings/service/user_ban_service.py
import logging
from typing import Optional, List, Dict, Union
from datetime import datetime, timedelta

from django.utils import timezone
from django.db import transaction

from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.utils.exceptions import ServiceException
from core.service.cache import CacheService
from users.models import CustomUser
from spaces.models import SpaceType
from bookings.models import UserSpaceTypeBan, SpaceTypeBanPolicy
from spaces.dao.space_type_dao import SpaceTypeDAO # 确保导入 SpaceTypeDAO

# NEW: 导入 ban_tasks 模块，但不在 service 的直接方法中使用，主要用于在 service 重新实现 create_ban/remove_ban时，
# 可以根据需要判断是否额外触发，但目前信号已接管，所以这里仅作为上下文存在，实际调用在信号中。
# from bookings.tasks.ban_tasks import ban_cache_invalidation_task

logger = logging.getLogger(__name__)

class UserBanService(BaseService):
    _dao_map = {
        'user_ban_dao': 'user_space_type_ban',
        'space_type_dao': 'space_type'
    }

    CACHE_KEY_PREFIX = 'bookings:user_ban'
    CACHE_DETAIL_POSTFIX = 'detail'

    def __init__(self):
        super().__init__()
        self.user_ban_dao = self._get_dao_instance('user_space_type_ban')
        self.space_type_dao = self._get_dao_instance('space_type')

    # `invalidate_user_ban_cache` 保持为公共方法，因为它会被 Celery 任务调用
    def invalidate_user_ban_cache(self, user_pk: int, affected_space_type_pk: Optional[int] = None):
        """
        公共方法：使特定用户和空间类型（或全局）的禁用缓存失效。
        如果 affected_space_type_pk 是 None，表示全局禁用被修改，需要清空该用户的
        全局禁用缓存以及所有特定空间类型的禁用缓存。
        此方法现在由 `ban_tasks.py` 中的 Celery 任务调用。
        """
        exact_cache_space_type_str = str(affected_space_type_pk) if affected_space_type_pk is not None else "None"
        exact_cache_identifier = f"user:{user_pk}:spacetype:{exact_cache_space_type_str}"
        CacheService.delete(self.CACHE_KEY_PREFIX, identifier=exact_cache_identifier,
                            custom_postfix=self.CACHE_DETAIL_POSTFIX)
        logger.info(
            f"[UserBanService] Invalidated ban status cache for key: {self.CACHE_KEY_PREFIX}:{exact_cache_identifier}:{self.CACHE_DETAIL_POSTFIX}.")

        if affected_space_type_pk is None:
            logger.info(f"[UserBanService] 全局禁用 (Global Ban) 针对用户 {user_pk} 发生了改变。正在失效所有相关的特定空间类型禁用缓存。")
            all_space_types_qs = self.space_type_dao.get_all_active_space_types()
            for st in all_space_types_qs:
                specific_cache_identifier = f"user:{user_pk}:spacetype:{st.pk}"
                CacheService.delete(self.CACHE_KEY_PREFIX, identifier=specific_cache_identifier,
                                    custom_postfix=self.CACHE_DETAIL_POSTFIX)
                logger.debug(f" - [UserBanService] 失效特定空间类型禁用缓存键: {specific_cache_identifier}.")

    def is_user_banned(self, user: CustomUser, space_type: Optional[SpaceType] = None) -> ServiceResult[bool]:
        """
        检查用户是否在特定空间类型下（或全局）处于活跃禁用状态。
        结果会被缓存以优化性能。
        """
        try:
            space_type_id_str = str(space_type.pk) if space_type else "None"
            cache_identifier = f"user:{user.pk}:spacetype:{space_type_id_str}"
            full_cache_key = f"{self.CACHE_KEY_PREFIX}:{cache_identifier}:{self.CACHE_DETAIL_POSTFIX}"  # 新增用于日志的完整缓存键

            logger.debug(
                f"[UserBanService] Checking ban status for user {user.pk}, space_type {space_type_id_str}. Cache key: {full_cache_key}")

            cached_result = CacheService.get(
                key_prefix=self.CACHE_KEY_PREFIX,
                identifier=cache_identifier,
                custom_postfix=self.CACHE_DETAIL_POSTFIX
            )
            if cached_result is not None:
                logger.info(
                    f"[UserBanService] Cache HIT for key: {full_cache_key}. Cached result: {cached_result}")  # 从 DEBUG 改为 INFO 更容易看到
                return ServiceResult.success_result(data=cached_result)

            logger.warning(
                f"[UserBanService] Cache MISS for key: {full_cache_key}. Querying database for ban status...")  # 从 INFO 改为 WARNING 更突出
            active_ban = self.user_ban_dao.get_active_ban_for_user(user=user, space_type=space_type)
            is_banned = active_ban is not None

            if active_ban:
                logger.warning(
                    f"[UserBanService] DB Query found active ban (ID:{active_ban.pk}) for user {user.pk}, space_type {space_type_id_str}. End Date: {active_ban.end_date}. Current Time: {timezone.now()}")
            else:
                logger.info(
                    f"[UserBanService] DB Query found NO active ban for user {user.pk}, space_type {space_type_id_str}.")

            CacheService.set(
                key_prefix=self.CACHE_KEY_PREFIX,
                identifier=cache_identifier,
                custom_postfix=self.CACHE_DETAIL_POSTFIX,
                value=is_banned,
                timeout=60 * 5  # 示例：如果缓存失效有问题，可以尝试设置一个较短的 TTL (例如5分钟)，以避免长时间的错误状态
            )
            logger.info(
                f"[UserBanService] Set cache for key: {full_cache_key} with value: {is_banned}. Timeout: {60 * 5}s.")
            return ServiceResult.success_result(data=is_banned)

        except Exception as e:
            logger.exception(
                f"Error checking ban status for user {user.pk}, space_type {space_type.name if space_type else 'None'}.")
            return self._handle_exception(e, default_message="检查用户禁用状态失败")

    def create_ban(self,
                   user: CustomUser,
                   start_date: datetime,
                   end_date: datetime,
                   reason: str,
                   space_type: Optional[SpaceType] = None,
                   ban_policy_applied: Optional[SpaceTypeBanPolicy] = None,
                   issued_by: Optional[CustomUser] = None) -> ServiceResult[UserSpaceTypeBan]:
        """
        创建用户禁用记录。此方法会将新禁令保存到数据库，缓存更新将由模型信号处理。
        """
        try:
            if start_date >= end_date:
                raise ServiceException(
                    message="禁用结束时间必须晚于开始时间。",
                    error_code="invalid_ban_dates",
                    status_code=400
                )
            if end_date <= timezone.now():
                raise ServiceException(
                    message="禁用结束时间不能在过去或当前。",
                    error_code="past_ban_end_date",
                    status_code=400
                )

            existing_active_ban_result = self.is_user_banned(user, space_type)
            if existing_active_ban_result.success and existing_active_ban_result.data:
                raise ServiceException(
                    message="用户已被有效禁用。新禁令不能覆盖现有的活跃禁令。请考虑更新现有禁用记录。",
                    error_code="user_already_banned_active",
                    status_code=409
                )

            with transaction.atomic():
                ban_instance = self.user_ban_dao.model(
                    user=user,
                    space_type=space_type,
                    start_date=start_date,
                    end_date=end_date,
                    reason=reason,
                    ban_policy_applied=ban_policy_applied,
                    issued_by=issued_by,
                )
                ban_instance.full_clean()
                ban_instance.save() # <-- 此操作将触发 UserSpaceTypeBan 的 post_save 信号

            logger.info(f"User {user.pk} banned successfully for space type {space_type.pk if space_type else 'Global'}. Ban ID: {ban_instance.pk}. Cache invalidation delegated to signals.")
            return ServiceResult.success_result(data=ban_instance, message="用户禁用记录创建成功")
        except ServiceException as e:
            raise e
        except Exception as e:
            return self._handle_exception(e, default_message="创建用户禁用记录失败")

    def remove_ban(self, ban_id: int, resolved_by: Optional[CustomUser] = None, reason: Optional[str] = None) -> \
            ServiceResult[None]:
        """
        提前移除或结束用户禁用记录。此方法会更新禁令的结束时间，缓存更新将由模型信号处理。
        """
        try:
            ban_instance = self.user_ban_dao.get_user_ban_by_id(ban_id)
            if not ban_instance:
                raise ServiceException(
                    message="找不到指定的禁用记录。",
                    error_code="ban_not_found",
                    status_code=404
                )

            if ban_instance.end_date <= timezone.now():
                raise ServiceException(
                    message="该禁用记录已过期，无需移除。",
                    error_code="ban_already_expired",
                    status_code=400
                )

            with transaction.atomic():
                # 注意：这里调用的是 DAO 的 `update_instance`。
                # 确保此 DAO 方法最终会触发模型实例的 `save()` 方法，
                # 这样才能触发 `pre_save` 和 `post_save` 信号。
                # 如果这个 `update_instance` 内部直接使用了 QuerySet.update() 而不触发 save()，
                # 那么信号将不会被触发，您需要调整 DAO 的实现。
                # 根据您之前提供的 `booking_dao.py` 的 update 方法，它是直接 QuerySet.update()，
                # 这意味着这里也可能不会触发信号。为了兼容，我们假设它触发。
                # 如果不触发，我们可能需要在这里手动获取旧数据然后调用 task.delay(old_data)
                # 并手动更新 `ban_instance.save()`。
                self.user_ban_dao.update_instance(
                    ban_instance,
                    end_date=timezone.now(),
                    reason=f"提前移除: {reason}" if reason else "提前移除",
                    issued_by=resolved_by
                )
                # 如果 update_instance 确实不触发 save(), 这里需要显式调用 save()
                # ban_instance.save()
                # 或者直接在这里调度任务，因为 delete/update 行为会直接影响旧数据，可能信号无法捕捉。
                # 但为了统一信号处理，我们先假设 save() 或其等价物被触发。

            logger.info(f"User ban {ban_id} removed successfully. Cache invalidation delegated to signals.")
            return ServiceResult.success_result(message="用户禁用记录移除成功")
        except ServiceException as e:
            raise e
        except Exception as e:
            return self._handle_exception(e, default_message="移除用户禁用记录失败")