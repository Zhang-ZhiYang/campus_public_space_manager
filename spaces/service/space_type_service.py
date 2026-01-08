# spaces/service/space_type_service.py
import logging
from typing import List
from django.db import transaction
from django.db.models import QuerySet

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException
from spaces.models import SpaceType
from django.contrib.auth import get_user_model
from guardian.shortcuts import get_objects_for_user

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


class SpaceTypeService(BaseService):
    _dao_map = {
        'space_type_dao': 'space_type',
    }

    def get_all_space_types(self, user: CustomUser) -> ServiceResult[QuerySet[SpaceType]]:
        """
        获取所有空间类型。系统管理员可以查看所有，其他用户可以查看公开的。
        """
        try:
            if user.is_superuser or getattr(user, 'is_system_admin', False):
                space_types = self.space_type_dao.get_all()
            else:
                space_types = self.space_type_dao.get_all()  # 假设普通用户也能看所有空间类型名称

            return ServiceResult.success_result(
                data=space_types,
                message="成功获取空间类型列表。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="获取空间类型列表失败。")

    def get_space_type_by_id(self, user: CustomUser, pk: int) -> ServiceResult[SpaceType]:
        """
        根据ID获取单个空间类型。
        """
        try:
            space_type = self.space_type_dao.get_by_id(pk)
            if not space_type:
                return ServiceResult.error_result(
                    message="空间类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # 权限检查：只有系统管理员或超级管理员可以查看 SpaceType 的详细配置
            if not (user.is_superuser or getattr(user, 'is_system_admin', False)):
                # 如果是普通用户或空间管理员，这里可以根据 SpaceType 是否有 is_public 字段再细分
                # 目前简单处理：认为只有高权限用户才能看详细配置
                return ServiceResult.error_result(
                    message=ForbiddenException.default_detail,
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

            return ServiceResult.success_result(
                data=space_type,
                message="成功获取空间类型详情。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="获取空间类型详情失败。")

    @transaction.atomic
    def create_space_type(self, user: CustomUser, space_type_data: dict) -> ServiceResult[SpaceType]:
        """
        创建新的空间类型。只有系统管理员可以操作。
        """
        if not (user.is_superuser or getattr(user, 'is_system_admin', False)):
            return ServiceResult.error_result(
                message=ForbiddenException.default_detail,
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )

        try:
            new_space_type = self.space_type_dao.create(**space_type_data)
            return ServiceResult.success_result(
                data=new_space_type,
                message="空间类型创建成功。",
                status_code=201
            )
        except Exception as e:
            return self._handle_exception(e, default_message="创建空间类型失败。")

    @transaction.atomic
    def update_space_type(self, user: CustomUser, pk: int, space_type_data: dict) -> ServiceResult[SpaceType]:
        """
        更新空间类型。只有系统管理员可以操作。
        """
        if not (user.is_superuser or getattr(user, 'is_system_admin', False)):
            return ServiceResult.error_result(
                message=ForbiddenException.default_detail,
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )

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
                data=updated_space_type,
                message="空间类型更新成功。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="更新空间类型失败。")

    @transaction.atomic
    def delete_space_type(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        """
        删除空间类型。只有系统管理员可以操作。
        在删除前需要检查是否有 Space 绑定到该 SpaceType。
        """
        if not (user.is_superuser or getattr(user, 'is_system_admin', False)):
            return ServiceResult.error_result(
                message=ForbiddenException.default_detail,
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )

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
            return self._handle_exception(e, default_message="删除空间类型失败。")