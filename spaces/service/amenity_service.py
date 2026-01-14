# spaces/service/amenity_service.py
import logging
from typing import List, Dict, Any, Optional
from django.db import transaction
from django.db.models import QuerySet
from core.service import BaseService, ServiceResult
from core.utils.exceptions import BadRequestException, NotFoundException, CustomAPIException, ForbiddenException # Added ForbiddenException
from spaces.models import Amenity
from django.contrib.auth import get_user_model
from core.cache import CacheService # Use the now updated CacheService

logger = logging.getLogger(__name__)
CustomUser = get_user_model()

class AmenityService(BaseService):
    """
    负责处理 Amenity 模型相关的业务逻辑。
    """
    _dao_map = {
        'amenity_dao': 'amenity',
    }
    _allowed_prefetch_related = []  # Amenity model is simple, no complex relations typically
    _allowed_select_related = []

    # NO @CacheService.cache_method here. List caching now happens in the View.
    def get_all_amenities(self, user: CustomUser) -> ServiceResult[QuerySet[Amenity]]:
        """
        获取所有设施类型列表的 QuerySet。
        DAO 负责基础数据获取和预加载。
        """
        try:
            amenities_qs = self.amenity_dao.get_all(
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )
            return ServiceResult.success_result(
                data=amenities_qs,  # Direct QuerySet
                message="成功获取设施类型列表。",
                status_code=200
            )
        except Exception as e:
            logger.exception("获取设施类型列表失败。")
            return self._handle_exception(e, default_message="获取设施类型列表失败。")

    @CacheService.cache_method(key_prefix='spaces:amenity:detail', identifier_arg='pk') # Explicit identifier_arg
    def get_amenity_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Dict[str, Any]]:
        """
        根据ID获取单个设施类型详情。
        此方法将从DAO获取Model实例，转换为Dict后进行缓存。
        """
        try:
            amenity = self.amenity_dao.get_by_id(
                pk,
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )
            if not amenity:
                raise NotFoundException(detail="设施类型未找到。")

            # 权限检查（如果需要，尽管Amenity通常是公开可看的）
            # if not user.is_superuser and not self._user_has_view_permission(user, amenity):
            #     raise PermissionDeniedException(detail="您没有权限查看此设施类型。")

            amenity_data = amenity.to_dict()  # 调用 .to_dict()
            return ServiceResult.success_result(
                data=amenity_data,
                message="成功获取设施类型详情。",
                status_code=200
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"获取设施类型详情失败 (ID: {pk})。")
            return self._handle_exception(e, default_message="获取设施类型详情失败。")

    @transaction.atomic
    def create_amenity(self, user: CustomUser, amenity_data: dict) -> ServiceResult[Dict[str, Any]]:
        """
        创建新的设施类型。
        """
        try:
            new_amenity = self.amenity_dao.create(**amenity_data)
            return ServiceResult.success_result(
                data=new_amenity.to_dict(),
                message="设施类型创建成功。",
                status_code=201
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"创建设施类型失败 (数据: {amenity_data})。")
            return self._handle_exception(e, default_message="创建设施类型失败。")

    @transaction.atomic
    def update_amenity(self, user: CustomUser, pk: int, amenity_data: dict) -> ServiceResult[Dict[str, Any]]:
        """
        更新设施类型。
        """
        try:
            amenity = self.amenity_dao.get_by_id(pk)
            if not amenity:
                raise NotFoundException(detail="设施类型未找到。")

            updated_amenity = self.amenity_dao.update(amenity, **amenity_data)
            return ServiceResult.success_result(
                data=updated_amenity.to_dict(),
                message="设施类型更新成功。",
                status_code=200
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新设施类型失败 (ID: {pk}, 数据: {amenity_data})。")
            return self._handle_exception(e, default_message="更新设施类型失败。")

    @transaction.atomic
    def delete_amenity(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        """
        删除设施类型。
        """
        try:
            amenity = self.amenity_dao.get_by_id(pk)
            if not amenity:
                raise NotFoundException(detail="设施类型未找到。")

            if amenity.bookable_instances.exists():
                # Fix: Remove 'errors' argument from BadRequestException
                raise BadRequestException(detail="存在关联的设施实例，无法删除此设施类型。请先解除所有设施实例与此类型的绑定。")

            self.amenity_dao.delete(amenity)
            return ServiceResult.success_result(
                message="设施类型删除成功。",
                status_code=204
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"删除设施类型失败 (ID: {pk})。")
            return self._handle_exception(e, default_message="删除设施类型失败。")