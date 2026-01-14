# spaces/service/space_type_service.py
import logging
from typing import List, Dict, Any
from django.db import transaction
# from django.db.models import QuerySet # 现在返回 List[Dict]
from core.service import BaseService, ServiceResult
from core.utils.exceptions import BadRequestException, NotFoundException, CustomAPIException
from spaces.models import SpaceType
from django.contrib.auth import get_user_model
from core.cache import CacheService # 导入 CacheService
# from django.forms.models import model_to_dict # 不再需要，使用模型自身的 .to_dict()

logger = logging.getLogger(__name__)
CustomUser = get_user_model()

class SpaceTypeService(BaseService):
    _dao_map = {
        'space_type_dao': 'space_type',
    }

    @CacheService.cache_method(key_prefix='spaces:spacetype:list_all', is_list_cache=True, list_fixed_custom_postfix='list_all')
    def get_all_space_types(self, user: CustomUser) -> ServiceResult[List[Dict[str, Any]]]: # 返回类型改为 List[Dict]
        """
        获取所有空间类型列表。所有认证用户可见所有空间类型列表。
        """
        try:
            space_types_qs = self.space_type_dao.get_all() # DAO 返回 QuerySet
            # 转换为字典列表，用于缓存
            space_types_data = [st.to_dict() for st in space_types_qs]
            return ServiceResult.success_result(
                data=space_types_data,
                message="成功获取空间类型列表。",
                status_code=200
            )
        except Exception as e:
            logger.exception("获取空间类型列表失败。")
            return self._handle_exception(e, default_message="获取空间类型列表失败。")

    @CacheService.cache_method(key_prefix='spaces:spacetype:detail')
    def get_space_type_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Dict[str, Any]]: # 返回类型改为 Dict
        """
        根据ID获取单个空间类型详情。所有认证用户可见空间类型详情。
        """
        try:
            space_type = self.space_type_dao.get_by_id(pk) # DAO 返回 Model 实例
            if not space_type:
                return ServiceResult.error_result(
                    message="空间类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )
            # 转换为字典，用于缓存
            space_type_data = space_type.to_dict()
            return ServiceResult.success_result(
                data=space_type_data,
                message="成功获取空间类型详情。",
                status_code=200
            )
        except Exception as e:
            logger.exception(f"获取空间类型详情失败 (ID: {pk})。")
            return self._handle_exception(e, default_message="获取空间类型详情失败。")

    @transaction.atomic
    def create_space_type(self, user: CustomUser, space_type_data: dict) -> ServiceResult[Dict[str, Any]]: # 返回类型改为 Dict
        """
        创建新的空间类型。权限已在视图层通过装饰器检查。
        创建成功后，依赖于 signal 触发缓存失效。
        """
        try:
            new_space_type = self.space_type_dao.create(**space_type_data)
            return ServiceResult.success_result(
                data=new_space_type.to_dict(), # 转换为字典
                message="空间类型创建成功。",
                status_code=201
            )
        except Exception as e:
            logger.exception(f"创建空间类型失败 (数据: {space_type_data})。")
            return self._handle_exception(e, default_message="创建空间类型失败。")

    @transaction.atomic
    def update_space_type(self, user: CustomUser, pk: int, space_type_data: dict) -> ServiceResult[Dict[str, Any]]: # 返回类型改为 Dict
        """
        更新空间类型。权限已在视图层通过装饰器检查。
        更新成功后，依赖于 signal 触发缓存失效。
        """
        try:
            space_type = self.space_type_dao.get_by_id(pk)
            if not space_type:
                return ServiceResult.error_result(
                    message="空间类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            updated_space_type = self.space_type_dao.update(space_type, **space_type_data)
            return ServiceResult.success_result(
                data=updated_space_type.to_dict(), # 转换为字典
                message="空间类型更新成功。",
                status_code=200
            )
        except Exception as e:
            logger.exception(f"更新空间类型失败 (ID: {pk}, 数据: {space_type_data})。")
            return self._handle_exception(e, default_message="更新空间类型失败。")

    @transaction.atomic
    def delete_space_type(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        """
        删除空间类型。权限已在视图层通过装饰器检查。
        删除成功后，依赖于 signal 触发缓存失效。
        """
        try:
            space_type = self.space_type_dao.get_by_id(pk)
            if not space_type:
                return ServiceResult.error_result(
                    message="空间类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            if space_type.spaces.exists():
                return ServiceResult.error_result(
                    message="存在关联的空间，无法删除此空间类型。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code,
                    errors=["请先解除所有空间与此类型的绑定。"]
                )

            self.space_type_dao.delete(space_type)
            return ServiceResult.success_result(
                message="空间类型删除成功。",
                status_code=204
            )
        except Exception as e:
            logger.exception(f"删除空间类型失败 (ID: {pk})。")
            return self._handle_exception(e, default_message="删除设施类型失败。")