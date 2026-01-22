# bookings/service/user_exemption_service.py
import logging
from typing import Optional, List, Dict, Union
from datetime import datetime

from django.utils import timezone
from django.db import transaction

from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.utils.exceptions import ServiceException
from core.service.cache import CacheService
from users.models import CustomUser
from spaces.models import SpaceType
from bookings.models import UserSpaceTypeExemption  # 导入豁免模型
from spaces.dao.space_type_dao import SpaceTypeDAO  # NEW: 导入 SpaceTypeDAO

# NEW: 导入 exemption_tasks 模块，但不在 service 的直接方法中使用，主要用于信号触发。
# from bookings.tasks.exemption_tasks import exemption_cache_invalidation_task

logger = logging.getLogger(__name__)


class UserExemptionService(BaseService):
    """
    负责处理用户豁免（Exemption）业务逻辑的服务。
    包括检查用户是否享有豁免。
    """
    _dao_map = {
        'user_exemption_dao': 'user_space_type_exemption',
        'space_type_dao': 'space_type'  # NEW: 添加 SpaceType DAO 的依赖
    }

    # 缓存键前缀
    CACHE_KEY_PREFIX = 'bookings:user_exemption'
    CACHE_DETAIL_POSTFIX = 'detail'  # 用于用户+空间类型组合的活跃豁免状态查询

    def __init__(self):
        super().__init__()
        self.user_exemption_dao = self._get_dao_instance('user_space_type_exemption')
        self.space_type_dao = self._get_dao_instance('space_type')  # NEW: 初始化 SpaceType DAO

    def invalidate_user_exemption_cache(self, user_pk: int, affected_space_type_pk: Optional[int] = None):
        """
        公共方法：使特定用户和空间类型（或全局）的豁免缓存失效。
        如果 affected_space_type_pk 是 None，表示全局豁免被修改，需要清空该用户的
        全局豁免缓存以及所有特定空间类型的豁免缓存。
        此方法现在由 `exemption_tasks.py` 中的 Celery 任务调用。
        """
        exact_cache_space_type_str = str(affected_space_type_pk) if affected_space_type_pk is not None else "None"
        exact_cache_identifier = f"user:{user_pk}:spacetype:{exact_cache_space_type_str}"
        CacheService.delete(self.CACHE_KEY_PREFIX, identifier=exact_cache_identifier,
                            custom_postfix=self.CACHE_DETAIL_POSTFIX)
        logger.info(
            f"[UserExemptionService] Invalidated exemption status cache for key: {self.CACHE_KEY_PREFIX}:{exact_cache_identifier}:{self.CACHE_DETAIL_POSTFIX}.")

        if affected_space_type_pk is None:  # 如果是全局豁免在生效或失效
            logger.info(
                f"[UserExemptionService] 全局豁免 (Global Exemption) 针对用户 {user_pk} 发生了改变。正在失效所有相关的特定空间类型豁免缓存。")
            all_space_types_qs = self.space_type_dao.get_all_active_space_types()
            for st in all_space_types_qs:
                specific_cache_identifier = f"user:{user_pk}:spacetype:{st.pk}"
                CacheService.delete(self.CACHE_KEY_PREFIX, identifier=specific_cache_identifier,
                                    custom_postfix=self.CACHE_DETAIL_POSTFIX)
                logger.debug(f" - [UserExemptionService] 失效特定空间类型豁免缓存键: {specific_cache_identifier}.")

    def is_user_exempted(self, user: CustomUser, space_type: Optional[SpaceType] = None) -> ServiceResult[bool]:
        """
        检查用户是否在特定空间类型下（或全局）处于活跃豁免状态。
        结果会被缓存以优化性能。

        :param user: CustomUser 实例。
        :param space_type: 可选的 SpaceType 实例，如果为 None 则检查全局豁免。
        :return: ServiceResult，data 字段为 bool，表示用户是否被豁免。
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
                    f"Cache HIT for exemption status for user {user.pk}, space_type {space_type_id_str}. Result: {cached_result}")
                return ServiceResult.success_result(data=cached_result)

            # 缓存未命中，进行数据库查询
            active_exemption = self.user_exemption_dao.get_active_exemption_for_user(user=user, space_type=space_type)
            is_exempted = active_exemption is not None

            # 将结果存入缓存
            CacheService.set(
                key_prefix=self.CACHE_KEY_PREFIX,
                identifier=cache_identifier,
                custom_postfix=self.CACHE_DETAIL_POSTFIX,
                value=is_exempted
            )
            logger.info(
                f"Calculated exemption status for user {user.pk}, space_type {space_type_id_str}. Is exempted: {is_exempted}. Cached result.")
            return ServiceResult.success_result(data=is_exempted)

        except Exception as e:
            logger.exception(
                f"Error checking exemption status for user {user.pk}, space_type {space_type.name if space_type else 'None'}.")
            return self._handle_exception(e, default_message="检查用户豁免状态失败")

    def create_exemption(self,
                         user: CustomUser,
                         exemption_reason: str,
                         space_type: Optional[SpaceType] = None,
                         start_date: Optional[datetime] = None,
                         end_date: Optional[datetime] = None,
                         granted_by: Optional[CustomUser] = None) -> ServiceResult[UserSpaceTypeExemption]:
        """
        创建用户豁免记录。
        """
        try:
            if start_date and end_date and start_date >= end_date:
                raise ServiceException(
                    message="豁免结束时间必须晚于开始时间。",
                    error_code="invalid_exemption_dates",
                    status_code=400
                )
            if end_date and end_date <= timezone.now():
                raise ServiceException(
                    message="豁免结束时间不能在过去或当前。",
                    error_code="past_exemption_end_date",
                    status_code=400
                )

            # 检查是否已经存在活跃的、针对相同用户和空间类型的豁免
            existing_active_exemption_result = self.is_user_exempted(user, space_type)
            if existing_active_exemption_result.success and existing_active_exemption_result.data:
                raise ServiceException(
                    message="用户已被有效豁免。新豁免不能覆盖现有的活跃豁免。请考虑更新现有豁免记录。",
                    error_code="user_already_exempted_active",
                    status_code=409  # Conflict
                )

            with transaction.atomic():
                exemption_instance = self.user_exemption_dao.model(
                    user=user,
                    space_type=space_type,
                    exemption_reason=exemption_reason,
                    start_date=start_date,
                    end_date=end_date,
                    granted_by=granted_by,
                )
                exemption_instance.full_clean()  # 执行模型层验证
                exemption_instance.save()  # 保存到数据库，会触发信号

            logger.info(
                f"User {user.pk} exempted successfully for space type {space_type.pk if space_type else 'Global'}. Exemption ID: {exemption_instance.pk}. Cache invalidation delegated to signals.")
            return ServiceResult.success_result(data=exemption_instance, message="用户豁免记录创建成功")
        except ServiceException as e:
            raise e
        except Exception as e:
            return self._handle_exception(e, default_message="创建用户豁免记录失败")

    def remove_exemption(self, exemption_id: int, revoked_by: Optional[CustomUser] = None,
                         reason: Optional[str] = None) -> \
            ServiceResult[None]:
        """
        提前移除或结束用户豁免记录。
        这通常意味着将豁免记录的 end_date 修改为当前时间或更早，使其失效。
        """
        try:
            exemption_instance = self.user_exemption_dao.get_user_exemption_by_id(exemption_id)
            if not exemption_instance:
                raise ServiceException(
                    message="找不到指定的豁免记录。",
                    error_code="exemption_not_found",
                    status_code=404
                )

            # 检查豁免是否已经过期，如果已经过期则无需操作
            if exemption_instance.end_date and exemption_instance.end_date <= timezone.now():
                raise ServiceException(
                    message="该豁免记录已过期，无需移除。",
                    error_code="exemption_already_expired",
                    status_code=400
                )

            with transaction.atomic():
                # 更新豁免记录的结束时间为现在，使其失效
                self.user_exemption_dao.update_instance(
                    exemption_instance,
                    end_date=timezone.now(),
                    exemption_reason=f"提前移除: {reason}" if reason else "提前移除",
                    granted_by=revoked_by  # 假设 revoked_by 可以代表最后操作人
                )
                # `update_instance` 假设会触发 `save()`，从而触发信号。

            logger.info(f"User exemption {exemption_id} removed successfully. Cache invalidation delegated to signals.")
            return ServiceResult.success_result(message="用户豁免记录移除成功")
        except ServiceException as e:
            raise e
        except Exception as e:
            return self._handle_exception(e, default_message="移除用户豁免记录失败")