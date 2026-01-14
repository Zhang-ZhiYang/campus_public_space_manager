# spaces/service/amenity_service.py
import logging
from typing import List, Dict, Any
from django.db import transaction
# from django.db.models import QuerySet # 不再返回 QuerySet
from core.service import BaseService, ServiceResult
from core.utils.exceptions import BadRequestException, NotFoundException, CustomAPIException
from spaces.models import Amenity
from django.contrib.auth import get_user_model
from core.cache import CacheService # 导入 CacheService
# from django.forms.models import model_to_dict # 不再需要，使用模型自身的 .to_dict()

logger = logging.getLogger(__name__)
CustomUser = get_user_model()

class AmenityService(BaseService):
    _dao_map = {
        'amenity_dao': 'amenity',
    }

    @CacheService.cache_method(key_prefix='spaces:amenity:list_all', is_list_cache=True, list_fixed_custom_postfix='list_all')
    def get_all_amenities(self, user: CustomUser) -> ServiceResult[List[Dict[str, Any]]]: # 返回类型改为 List[Dict]
        """
        获取所有设施类型列表。
        所有认证用户可见所有设施类型列表。
        """
        try:
            amenities_qs = self.amenity_dao.get_all()
            amenities_data = [amenity.to_dict() for amenity in amenities_qs] # 调用 .to_dict()
            return ServiceResult.success_result(
                data=amenities_data,
                message="成功获取设施类型列表。",
                status_code=200
            )
        except Exception as e:
            logger.exception("获取设施类型列表失败。")
            return self._handle_exception(e, default_message="获取设施类型列表失败。")

    @CacheService.cache_method(key_prefix='spaces:amenity:detail')
    def get_amenity_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Dict[str, Any]]: # 返回类型改为 Dict
        """
        根据ID获取单个设施类型详情。
        所有认证用户可见设施类型详情。
        """
        try:
            amenity = self.amenity_dao.get_by_id(pk)
            if not amenity:
                return ServiceResult.error_result(
                    message="设施类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )
            amenity_data = amenity.to_dict() # 调用 .to_dict()
            return ServiceResult.success_result(
                data=amenity_data,
                message="成功获取设施类型详情。",
                status_code=200
            )
        except Exception as e:
            logger.exception(f"获取设施类型详情失败 (ID: {pk})。")
            return self._handle_exception(e, default_message="获取设施类型详情失败。")

    @transaction.atomic
    def create_amenity(self, user: CustomUser, amenity_data: dict) -> ServiceResult[Dict[str, Any]]: # 返回类型改为 Dict
        """
        创建新的设施类型。权限已在视图层通过装饰器检查。
        创建成功后，异步触发缓存失效。
        """
        try:
            new_amenity = self.amenity_dao.create(**amenity_data)
            return ServiceResult.success_result(
                data=new_amenity.to_dict(), # 调用 .to_dict()
                message="设施类型创建成功。",
                status_code=201
            )
        except Exception as e:
            logger.exception(f"创建设施类型失败 (数据: {amenity_data})。")
            return self._handle_exception(e, default_message="创建设施类型失败。")

    @transaction.atomic
    def update_amenity(self, user: CustomUser, pk: int, amenity_data: dict) -> ServiceResult[Dict[str, Any]]: # 返回类型改为 Dict
        """
        更新设施类型。权限已在视图层通过装饰器检查。
        更新成功后，异步触发缓存失效。
        """
        try:
            amenity = self.amenity_dao.get_by_id(pk)
            if not amenity:
                return ServiceResult.error_result(
                    message="设施类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            updated_amenity = self.amenity_dao.update(amenity, **amenity_data)
            return ServiceResult.success_result(
                data=updated_amenity.to_dict(), # 调用 .to_dict()
                message="设施类型更新成功。",
                status_code=200
            )
        except Exception as e:
            logger.exception(f"更新设施类型失败 (ID: {pk}, 数据: {amenity_data})。")
            return self._handle_exception(e, default_message="更新设施类型失败。")

    @transaction.atomic
    def delete_amenity(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        """
        删除设施类型。权限已在视图层通过装饰器检查。
        删除成功后，异步触发缓存失效。
        """
        try:
            amenity = self.amenity_dao.get_by_id(pk)
            if not amenity:
                return ServiceResult.error_result(
                    message="设施类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            if amenity.bookable_instances.exists():
                return ServiceResult.error_result(
                    message="存在关联的设施实例，无法删除此设施类型。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code,
                    errors=["请先解除所有设施实例与此类型的绑定。"]
                )

            self.amenity_dao.delete(amenity)
            return ServiceResult.success_result(
                message="设施类型删除成功。",
                status_code=204
            )
        except Exception as e:
            logger.exception(f"删除设施类型失败 (ID: {pk})。")
            return self._handle_exception(e, default_message="删除设施类型失败。")