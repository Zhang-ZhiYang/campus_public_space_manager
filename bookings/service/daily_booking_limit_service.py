# bookings/service/daily_booking_limit_service.py
import logging
from typing import Optional, List, Dict

from django.db.models import QuerySet
from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.service.cache import CacheService  # 导入 CacheService
from users.models import CustomUser  # 导入 CustomUser 模型
from spaces.models import SpaceType  # 导入 SpaceType 模型
from django.contrib.auth.models import Group  # 导入 Group 模型

logger = logging.getLogger(__name__)


class DailyBookingLimitService(BaseService):
    """
    负责处理每日预订限制业务逻辑的服务。
    包括获取用户在特定空间类型下适用的每日预订次数限制。
    """
    _dao_map = {
        'daily_booking_limit_dao': 'daily_booking_limit'
    }

    # Cache key prefix for combined user group + space_type daily limits
    CACHE_KEY_PREFIX = 'bookings:daily_limit'
    CACHE_CUSTOM_POSTFIX = 'effective_by_group_spacetype'

    # 默认值，如果找不到任何限制规则，则不限制
    NO_LIMIT = 0

    def __init__(self):
        super().__init__()
        self.daily_booking_limit_dao = self._get_dao_instance('daily_booking_limit')

    def get_effective_daily_limit(self, user: CustomUser, space_type: Optional[SpaceType] = None) -> int:
        """
        获取用户在特定空间类型下（或全局）适用的每日最大预订次数限制。
        该方法会考虑用户的所属用户组以及限制规则的优先级，并利用缓存。

        :param user: CustomUser 实例。
        :param space_type: 可选的 SpaceType 实例，如果为 None 则获取全局限制。
        :return: 每日最大预订次数限制（int），0 表示没有限制。
        """
        try:
            # 1. 构建缓存键
            space_type_id = space_type.pk if space_type else None
            # 注意：用户所属的组可能会变，所以缓存键需要包含 user.pk或user.group_ids
            # 但针对 daily_limit，我们更关心的是 `groups_hash`, 因为规则是针对组的

            # 获取用户的所有组ID，并排序以确保哈希值一致
            user_group_ids = sorted(user.groups.values_list('pk', flat=True))

            cache_identifier_str = f"user:{user.pk}:spacetype:{space_type_id}:groups:{hash(tuple(user_group_ids))}"

            # 尝试从缓存中获取结果
            cached_limit = CacheService.get(
                key_prefix=self.CACHE_KEY_PREFIX,
                identifier=cache_identifier_str,  # 使用组合字符串作为 identifier
                custom_postfix=self.CACHE_CUSTOM_POSTFIX,  # 使用固定后缀
            )
            if cached_limit is not None:
                logger.debug(f"Cache HIT for effective daily limit for user {user.pk}, space_type {space_type_id}")
                return cached_limit

            logger.info(
                f"Calculating effective daily limit for user {user.pk}, space_type {space_type_id} (Cache MISS).")

            # 2. 如果缓存未命中，则计算
            groups = user.groups.all()  # 获取用户所属的所有 Group
            group_ids = [group.pk for group in groups]

            if not group_ids:
                logger.info(f"User {user.pk} has no groups. Returning no limit ({self.NO_LIMIT}).")
                CacheService.set(
                    key_prefix=self.CACHE_KEY_PREFIX,
                    identifier=cache_identifier_str,
                    custom_postfix=self.CACHE_CUSTOM_POSTFIX,
                    value=self.NO_LIMIT
                )
                return self.NO_LIMIT

            # 获取所有适用的限制规则：包括全局的 (space_type=None) 和特定 space_type 的
            # DAO 已经按优先级和 max_bookings 排序
            applicable_limits = self.daily_booking_limit_dao.get_applicable_limits_for_groups_and_spacetype(
                group_ids=group_ids,
                space_type=space_type
            )

            effective_limit = self.NO_LIMIT  # 默认没有限制

            # 遍历所有适用的限制规则，找出最严格的（优先级最高且 max_bookings 最低）
            # 由于 DAO 已经按 -priority, max_bookings 排序，第一个非0结果就是最严格的
            # 遍历会确保找到优先级最高的规则
            for limit_rule in applicable_limits:
                if limit_rule.max_bookings > 0:  # 0 表示不限制，只考虑有具体限制的规则
                    # 如果是全局限制（space_type为None），记录为备选
                    if limit_rule.space_type is None:
                        # 全局（空间类型为 None）规则通常优先级较低，但如果没找到更具体的，也会用
                        # 这里取第一个，因为已经排过序
                        effective_limit = limit_rule.max_bookings
                        break  # 因为已经按优先级排序，所以第一个有效的限制就是最严格或最适用的
                    # 如果是特定空间类型的限制
                    elif space_type and limit_rule.space_type.pk == space_type.pk:
                        # 特定空间类型的限制比全局限制更具体，优先级更高
                        effective_limit = limit_rule.max_bookings
                        break  # 找到了最匹配的规则，可以退出

            # 如果没有找到任何有具体限制的规则，或者找到的规则 max_bookings 为 0，则默认为不限制
            if effective_limit == self.NO_LIMIT and applicable_limits.exists():
                # 检查是否存在明确设置为 0 的规则，如果存在且是最高优先级，则表示不限制
                highest_priority_rule = applicable_limits.first()
                if highest_priority_rule and highest_priority_rule.max_bookings == 0:
                    effective_limit = self.NO_LIMIT

            # 3. 将结果存入缓存
            CacheService.set(
                key_prefix=self.CACHE_KEY_PREFIX,
                identifier=cache_identifier_str,
                custom_postfix=self.CACHE_CUSTOM_POSTFIX,
                value=effective_limit
            )
            return effective_limit

        except Exception as e:
            logger.exception(
                f"Error calculating effective daily booking limit for user {user.pk}, space_type {space_type.pk if space_type else 'None'}: {e}")
            # 发生异常时，默认不限制，避免阻碍用户操作
            return self.NO_LIMIT