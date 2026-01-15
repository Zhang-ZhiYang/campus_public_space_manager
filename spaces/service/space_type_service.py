# spaces/service/space_type_service.py
import logging
from typing import List, Dict, Any, Optional
from django.db import transaction
from django.db.models import QuerySet
from core.service import BaseService, ServiceResult
from core.utils.exceptions import BadRequestException, NotFoundException, CustomAPIException, ForbiddenException # Added ForbiddenException
from spaces.models import SpaceType
from django.contrib.auth import get_user_model
from core.cache import CacheService # Use the now updated CacheService

logger = logging.getLogger(__name__)
CustomUser = get_user_model()

class SpaceTypeService(BaseService):
    """
    负责处理 SpaceType 模型相关的业务逻辑。
    """
    _dao_map = {
        'space_type_dao': 'space_type',
    }
    _allowed_prefetch_related = []
    _allowed_select_related = []

    # NO @CacheService.cache_method here. List caching now happens in the View.
    def get_all_space_types(self, user: CustomUser) -> ServiceResult[QuerySet[SpaceType]]:
        """
        获取所有空间类型列表的 QuerySet。
        DAO 负责基础数据获取和预加载。
        """
        try:
            # SpaceType 列表默认是全局的，不根据用户过滤。
            # 如果未来需要用户权限过滤，此处也要传入 user 并调整 DAO。
            space_types_qs = self.space_type_dao.get_all(
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )
            return ServiceResult.success_result(
                data=space_types_qs,  # Direct QuerySet
                message="成功获取空间类型列表。",
                status_code=200
            )
        except Exception as e:
            logger.exception("获取空间类型列表失败。")
            return self._handle_exception(e, default_message="获取空间类型列表失败。")

    @CacheService.cache_method(key_prefix='spaces:spacetype') # General key_prefix for SpaceType model
    def get_space_type_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Dict[str, Any]]:
        """
        根据ID获取单个空间类型详情。
        此方法将从DAO获取Model实例，转换为Dict后进行缓存。
        """
        try:
            space_type = self.space_type_dao.get_by_id(
                pk,
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )
            if not space_type:
                raise NotFoundException(detail="空间类型未找到。")

            space_type_data = space_type.to_dict()
            return ServiceResult.success_result(
                data=space_type_data,
                message="成功获取空间类型详情。",
                status_code=200
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"获取空间类型详情失败 (ID: {pk})。")
            return self._handle_exception(e, default_message="获取空间类型详情失败。")

    @transaction.atomic
    def create_space_type(self, user: CustomUser, space_type_data: dict) -> ServiceResult[Dict[str, Any]]:
        """
        创建新的空间类型。
        """
        try:
            new_space_type = self.space_type_dao.create(**space_type_data)
            return ServiceResult.success_result(
                data=new_space_type.to_dict(),
                message="空间类型创建成功。",
                status_code=201
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"创建空间类型失败 (数据: {space_type_data})。")
            return self._handle_exception(e, default_message="创建空间类型失败。")

    @transaction.atomic
    def update_space_type(self, user: CustomUser, pk: int, space_type_data: dict) -> ServiceResult[Dict[str, Any]]:
        """
        更新空间类型。
        """
        try:
            space_type = self.space_type_dao.get_by_id(pk)
            if not space_type:
                raise NotFoundException(detail="空间类型未找到。")

            updated_space_type = self.space_type_dao.update(space_type, **space_type_data)
            return ServiceResult.success_result(
                data=updated_space_type.to_dict(),
                message="空间类型更新成功。",
                status_code=200
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新空间类型失败 (ID: {pk}, 数据: {space_type_data})。")
            return self._handle_exception(e, default_message="更新空间类型失败。")

    @transaction.atomic
    def delete_space_type(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        """
        删除空间类型。
        """
        try:
            space_type = self.space_type_dao.get_by_id(pk)
            if not space_type:
                raise NotFoundException(detail="空间类型未找到。")

            if space_type.spaces.exists():
                raise BadRequestException(detail="存在关联的空间，无法删除此空间类型。请先解除所有空间与此类型的绑定。")

            self.space_type_dao.delete(space_type)
            return ServiceResult.success_result(
                message="空间类型删除成功。",
                status_code=204
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"删除空间类型失败 (ID: {pk})。")
            return self._handle_exception(e, default_message="删除设施类型失败。")