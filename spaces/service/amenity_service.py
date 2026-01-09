# spaces/service/amenity_service.py
import logging
from typing import List
from django.db import transaction
from django.db.models import QuerySet

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException
from spaces.models import Amenity
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
CustomUser = get_user_model()

class AmenityService(BaseService):
    _dao_map = {
        'amenity_dao': 'amenity',
    }

    def get_all_amenities(self, user: CustomUser) -> ServiceResult[QuerySet[Amenity]]:
        """
        获取所有设施类型列表。
        此接口在 DRF 视图层已通过 `IsAuthenticated` 权限类验证，
        因此只要用户登录即可见，服务层无需额外权限检查。
        """
        try:
            amenities = self.amenity_dao.get_all()
            return ServiceResult.success_result(
                data=amenities,
                message="成功获取设施类型列表。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="获取设施类型列表失败。")

    def get_amenity_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Amenity]:
        """
        根据ID获取单个设施类型详情。
        此接口在 DRF 视图层已通过 `IsAuthenticated` 权限类验证，
        因此只要用户登录即可见，服务层无需额外权限检查。
        """
        try:
            amenity = self.amenity_dao.get_by_id(pk)
            if not amenity:
                return ServiceResult.error_result(
                    message="设施类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )
            return ServiceResult.success_result(
                data=amenity,
                message="成功获取设施类型详情。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="获取设施类型详情失败。")

    @transaction.atomic
    def create_amenity(self, user: CustomUser, amenity_data: dict) -> ServiceResult[Amenity]:
        """
        创建新的设施类型。
        允许系统管理员、超级管理员以及空间管理员操作。
        """
        try:
            new_amenity = self.amenity_dao.create(**amenity_data)
            return ServiceResult.success_result(
                data=new_amenity,
                message="设施类型创建成功。",
                status_code=201
            )
        except Exception as e:
            return self._handle_exception(e, default_message="创建设施类型失败。")

    @transaction.atomic
    def update_amenity(self, user: CustomUser, pk: int, amenity_data: dict) -> ServiceResult[Amenity]:
        """
        更新设施类型。
        允许系统管理员、超级管理员以及空间管理员操作。
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
                data=updated_amenity,
                message="设施类型更新成功。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="更新设施类型失败。")

    @transaction.atomic
    def delete_amenity(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        """
        删除设施类型。
        允许系统管理员、超级管理员以及空间管理员操作。
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
            return self._handle_exception(e, default_message="删除设施类型失败。")