# spaces/service/amenity_service.py
import logging
from typing import List
from django.db import transaction
from django.db.models import QuerySet

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException
from spaces.models import Amenity
from django.contrib.auth import get_user_model
# from guardian.shortcuts import get_objects_for_user # 如果没有用到，可以注释掉以保持代码简洁

logger = logging.getLogger(__name__)
CustomUser = get_user_model()

class AmenityService(BaseService):
    _dao_map = {
        'amenity_dao': 'amenity',
    }

    def get_all_amenities(self, user: CustomUser) -> ServiceResult[QuerySet[Amenity]]:
        """
        获取所有设施类型。
        默认不对普通用户进行额外权限过滤，任何人都可以看到有哪些类型的设施（只返回列表）。
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
        根据ID获取单个设施类型。
        现在允许系统管理员、超级管理员以及拥有 'spaces.view_amenity' 权限的用户查看详情。
        """
        try:
            amenity = self.amenity_dao.get_by_id(pk)
            if not amenity:
                return ServiceResult.error_result(
                    message="设施类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # 新增权限检查：允许系统管理员、超级管理员或拥有 'spaces.view_amenity' 权限的用户访问
            if not (user.is_superuser or
                    getattr(user, 'is_system_admin', False) or
                    user.has_perm('spaces.view_amenity')): # <--- 修改点
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
        创建新的设施类型。
        现在允许系统管理员、超级管理员以及拥有 'spaces.add_amenity' 权限的用户操作。
        """
        # 新增权限检查：允许系统管理员、超级管理员或拥有 'spaces.add_amenity' 权限的用户访问
        if not (user.is_superuser or
                getattr(user, 'is_system_admin', False) or
                user.has_perm('spaces.add_amenity')): # <--- 修改点
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
        更新设施类型。
        现在允许系统管理员、超级管理员以及拥有 'spaces.change_amenity' 权限的用户操作。
        """
        # 新增权限检查：允许系统管理员、超级管理员或拥有 'spaces.change_amenity' 权限的用户访问
        if not (user.is_superuser or
                getattr(user, 'is_system_admin', False) or
                user.has_perm('spaces.change_amenity')): # <--- 修改点
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
        删除设施类型。
        现在允许系统管理员、超级管理员以及拥有 'spaces.delete_amenity' 权限的用户操作。
        在删除前需要检查是否有 BookableAmenity 绑定到该 Amenity。
        """
        # 新增权限检查：允许系统管理员、超级管理员或拥有 'spaces.delete_amenity' 权限的用户访问
        if not (user.is_superuser or
                getattr(user, 'is_system_admin', False) or
                user.has_perm('spaces.delete_amenity')): # <--- 修改点
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