# spaces/service/amenity_service.py
import logging
from typing import List
from django.db import transaction
from django.db.models import QuerySet

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException
from spaces.models import Amenity
from django.contrib.auth import get_user_model
from guardian.shortcuts import get_objects_for_user

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


class AmenityService(BaseService):
    _dao_map = {
        'amenity_dao': 'amenity',
    }

    def get_all_amenities(self, user: CustomUser) -> ServiceResult[QuerySet[Amenity]]:
        """
        获取所有设施类型。
        """
        try:
            # 设施类型本身不对普通用户进行额外权限过滤，任何人都可以看到有哪些类型的设施
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
        根据ID获取单个设施类型。
        """
        try:
            amenity = self.amenity_dao.get_by_id(pk)
            if not amenity:
                return ServiceResult.error_result(
                    message="设施类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # 目前假设只有系统管理员或超级管理员可以查看设施类型的详细配置
            # 如果需要根据 Space 的权限来展示 Amenity 类型（例如，SpaceManager 可以看对应 Space 的 Amenity 类型），
            # 需要在 Amenity model 增加对 Space 的关联或者其他逻辑。
            # 目前，保留Amenity Type的详情只有高权限用户可见的设定。
            if not (user.is_superuser or getattr(user, 'is_system_admin', False)):
                return ServiceResult.error_result(
                    message=ForbiddenException.default_detail,
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
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
        创建新的设施类型。只有系统管理员可以操作。
        """
        if not (user.is_superuser or getattr(user, 'is_system_admin', False)):
            return ServiceResult.error_result(
                message=ForbiddenException.default_detail,
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )

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
        更新设施类型。只有系统管理员可以操作。
        """
        if not (user.is_superuser or getattr(user, 'is_system_admin', False)):
            return ServiceResult.error_result(
                message=ForbiddenException.default_detail,
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )

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
        删除设施类型。只有系统管理员可以操作。
        在删除前需要检查是否有 BookableAmenity 绑定到该 Amenity。
        """
        if not (user.is_superuser or getattr(user, 'is_system_admin', False)):
            return ServiceResult.error_result(
                message=ForbiddenException.default_detail,
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )

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