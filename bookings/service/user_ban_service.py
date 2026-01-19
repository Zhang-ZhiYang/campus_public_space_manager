# bookings/service/user_ban_service.py
import logging
from typing import Optional, List, Dict, Union
from datetime import datetime, timedelta

from django.utils import timezone

from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.utils.exceptions import ServiceException
from core.service.cache import CacheService  # 导入 CacheService
from users.models import CustomUser
from spaces.models import SpaceType
from bookings.models import UserSpaceTypeBan, SpaceTypeBanPolicy  # 导入 Ban Policy model

logger = logging.getLogger(__name__)


class UserBanService(BaseService):
    """
    负责处理用户禁用（Ban）业务逻辑的服务。
    包括检查用户是否被禁用，以及创建/移除禁用记录。
    """
    _dao_map = {
        'user_ban_dao': 'user_space_type_ban'  # 对应 DAOFactory 中的注册名称
    }

    # 缓存键前缀
    CACHE_KEY_PREFIX = 'bookings:user_ban'
    CACHE_DETAIL_POSTFIX = 'detail'  # 用于用户+空间类型组合的活跃禁用状态查询

    def __init__(self):
        super().__init__()
        self.user_ban_dao = self._get_dao_instance('user_space_type_ban')

    def is_user_banned(self, user: CustomUser, space_type: Optional[SpaceType] = None) -> ServiceResult[bool]:
        """
        检查用户是否在特定空间类型下（或全局）处于活跃禁用状态。
        结果会被缓存以优化性能。

        :param user: CustomUser 实例。
        :param space_type: 可选的 SpaceType 实例，如果为 None 则检查全局禁用。
        :return: ServiceResult，data 字段为 bool，表示用户是否被禁用。
        """
        try:
            # 构建缓存键：结合用户ID和空间类型ID
            space_type_id_str = str(space_type.pk) if space_type else "None"
            cache_identifier = f"user:{user.pk}:spacetype:{space_type_id_str}"

            # 尝试从缓存获取
            cached_result = CacheService.get(
                key_prefix=self.CACHE_KEY_PREFIX,
                identifier=cache_identifier,
                custom_postfix=self.CACHE_DETAIL_POSTFIX
            )
            if cached_result is not None:
                logger.debug(
                    f"Cache HIT for ban status for user {user.pk}, space_type {space_type_id_str}. Result: {cached_result}")
                return ServiceResult.success_result(data=cached_result)

            # 缓存未命中，进行数据库查询
            active_ban = self.user_ban_dao.get_active_ban_for_user(user=user, space_type=space_type)
            is_banned = active_ban is not None

            # 将结果存入缓存
            CacheService.set(
                key_prefix=self.CACHE_KEY_PREFIX,
                identifier=cache_identifier,
                custom_postfix=self.CACHE_DETAIL_POSTFIX,
                value=is_banned
            )
            logger.info(
                f"Calculated ban status for user {user.pk}, space_type {space_type_id_str}. Is banned: {is_banned}. Cached result.")
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
        创建用户禁用记录。

        :param user: 被禁用的用户实例。
        :param start_date: 禁用开始时间。
        :param end_date: 禁用结束时间。
        :param reason: 禁用原因。
        :param space_type: 可选的 SpaceType 实例，表示特定空间类型的禁用；None 表示全局禁用。
        :param ban_policy_applied: 触发本次禁用的策略实例（如果由策略自动触发）。
        :param issued_by: 执行本次禁用的管理员用户实例。
        :return: ServiceResult，data 字段为创建的 UserSpaceTypeBan 实例。
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

            # 检查是否已经存在活跃的、针对相同用户和空间类型的禁用
            # 避免重复创建 active ban
            existing_active_ban_result = self.is_user_banned(user, space_type)
            if existing_active_ban_result.success and existing_active_ban_result.data:
                # 理论上，创建新的 ban 应该替换或延长现有的。这里简单返回错误。
                # 实际业务中可能需要更复杂的逻辑，如更新现有 ban。
                raise ServiceException(
                    message="用户已被有效禁用。请考虑更新现有禁用记录。",
                    error_code="user_already_banned",
                    status_code=409  # Conflict
                )

            ban_instance = self.user_ban_dao.model(
                user=user,
                space_type=space_type,
                start_date=start_date,
                end_date=end_date,
                reason=reason,
                ban_policy_applied=ban_policy_applied,
                issued_by=issued_by,
            )
            ban_instance.full_clean()  # 执行模型层验证
            ban_instance.save()  # 保存到数据库

            # 禁用创建或更新后，需要使相应用户的缓存失效
            space_type_id_str = str(space_type.pk) if space_type else "None"
            cache_identifier = f"user:{user.pk}:spacetype:{space_type_id_str}"
            CacheService.delete(self.CACHE_KEY_PREFIX, identifier=cache_identifier,
                                custom_postfix=self.CACHE_DETAIL_POSTFIX)
            logger.info(f"Invalidated ban status cache for user {user.pk}, space_type {space_type_id_str}.")

            logger.info(f"User {user.pk} banned successfully for space type {space_type_id_str}.")
            return ServiceResult.success_result(data=ban_instance, message="用户禁用记录创建成功")
        except Exception as e:
            return self._handle_exception(e, default_message="创建用户禁用记录失败")

    def remove_ban(self, ban_id: int, resolved_by: Optional[CustomUser] = None, reason: Optional[str] = None) -> \
    ServiceResult[None]:
        """
        提前移除或结束用户禁用记录。
        这通常意味着将禁用记录的 end_date 修改为当前时间或更早，使其失效。

        :param ban_id: 要移除的禁用记录的ID。
        :param resolved_by: 执行移除操作的管理员用户实例。
        :param reason: 移除禁用的原因。
        :return: ServiceResult
        """
        try:
            ban_instance = self.user_ban_dao.get_user_ban_by_id(ban_id)
            if not ban_instance:
                raise ServiceException(
                    message="找不到指定的禁用记录。",
                    error_code="ban_not_found",
                    status_code=404
                )

            # 检查禁用是否已经过期，如果已经过期则无需操作
            if ban_instance.end_date <= timezone.now():
                raise ServiceException(
                    message="该禁用记录已过期，无需移除。",
                    error_code="ban_already_expired",
                    status_code=400
                )

            # 更新禁用记录的结束时间为现在，使其失效
            ban_instance.end_date = timezone.now()
            ban_instance.reason = f"提前移除: {reason}" if reason else "提前移除"
            ban_instance.issued_by = resolved_by  # 理论上应该是更新issued_by或新增一个resolved_by字段，这里复用了issued_by

            self.user_ban_dao.update_instance(
                ban_instance,
                end_date=timezone.now(),
                reason=f"提前移除: {reason}" if reason else "提前移除",
                issued_by=resolved_by  # 假设issued_by可以代表最后操作人
                # 如果模型有 'removed_by'/'removed_at' 更好
            )  # 使用 DAO 的 update_instance 方法

            # 禁用移除后，需要使相应用户的缓存失效
            space_type_id_str = str(ban_instance.space_type.pk) if ban_instance.space_type else "None"
            cache_identifier = f"user:{ban_instance.user.pk}:spacetype:{space_type_id_str}"
            CacheService.delete(self.CACHE_KEY_PREFIX, identifier=cache_identifier,
                                custom_postfix=self.CACHE_DETAIL_POSTFIX)
            logger.info(
                f"Invalidated ban status cache for user {ban_instance.user.pk}, space_type {space_type_id_str}.")

            logger.info(f"User ban {ban_id} removed successfully.")
            return ServiceResult.success_result(message="用户禁用记录移除成功")
        except Exception as e:
            return self._handle_exception(e, default_message="移除用户禁用记录失败")