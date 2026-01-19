# bookings/service/user_exemption_service.py
import logging
from typing import Optional, List, Dict, Union
from datetime import datetime

from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.utils.exceptions import ServiceException
from core.service.cache import CacheService # 导入 CacheService
from users.models import CustomUser
from spaces.models import SpaceType
from bookings.models import UserSpaceTypeExemption # 导入豁免模型

logger = logging.getLogger(__name__)

class UserExemptionService(BaseService):
    """
    负责处理用户豁免（Exemption）业务逻辑的服务。
    包括检查用户是否享有豁免。
    """
    _dao_map = {
        'user_exemption_dao': 'user_space_type_exemption' # 对应 DAOFactory 中的注册名称
    }

    # 缓存键前缀
    CACHE_KEY_PREFIX = 'bookings:user_exemption'
    CACHE_DETAIL_POSTFIX = 'detail' # 用于用户+空间类型组合的活跃豁免状态查询

    def __init__(self):
        super().__init__()
        self.user_exemption_dao = self._get_dao_instance('user_space_type_exemption')

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
                logger.debug(f"Cache HIT for exemption status for user {user.pk}, space_type {space_type_id_str}. Result: {cached_result}")
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
            logger.info(f"Calculated exemption status for user {user.pk}, space_type {space_type_id_str}. Is exempted: {is_exempted}. Cached result.")
            return ServiceResult.success_result(data=is_exempted)

        except Exception as e:
            logger.exception(f"Error checking exemption status for user {user.pk}, space_type {space_type.name if space_type else 'None'}.")
            return self._handle_exception(e, default_message="检查用户豁免状态失败")

    # (可选) 未来可在此处添加 create_exemption / update_exemption / delete_exemption 方法
    # def create_exemption(...):
    #     pass

    # def remove_exemption(...):
    #     pass