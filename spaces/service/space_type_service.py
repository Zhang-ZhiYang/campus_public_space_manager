# spaces/service/space_type_service.py
import logging
from typing import List
from django.db import transaction
from django.db.models import QuerySet

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException
from spaces.models import SpaceType
from django.contrib.auth import get_user_model

# from guardian.shortcuts import get_objects_for_user # 如果没有用到，可以注释掉以保持代码简洁

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


class SpaceTypeService(BaseService):
    _dao_map = {
        'space_type_dao': 'space_type',
    }

    def get_all_space_types(self, user: CustomUser) -> ServiceResult[QuerySet[SpaceType]]:
        """
        获取所有空间类型。
        现在允许系统管理员、超级管理员以及空间管理员查看所有空间类型列表。
        普通用户也可以查看（因为通常空间类型配置是公开的，但详情可能需要权限）。
        """
        try:
            # 简化权限检查：is_space_manager 已包含 is_system_admin 和 is_superuser
            # 如果是任何认证用户都可以看列表，这里就没有 if
            # 如果仅管理员角色可以看列表，则如下：
            # if not user.is_authenticated or not user.is_space_manager: # 如果需要 SpaceManager 才能看列表
            #     return ServiceResult.error_result(
            #         message=ForbiddenException.default_detail,
            #         error_code=ForbiddenException.default_code,
            #         status_code=ForbiddenException.status_code
            #     )

            # 目前Admin view的has_module_permission也限制为 is_superuser or is_system_admin。
            # 如果要同步 Admin 的 has_module_permission，则也应加判断
            if not (user.is_superuser or user.is_system_admin):  # 与 Admin 的 has_module_permission 保持一致
                return ServiceResult.error_result(
                    message=ForbiddenException.default_detail,
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

            space_types = self.space_type_dao.get_all()
            return ServiceResult.success_result(
                data=space_types,
                message="成功获取空间类型列表。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="获取空间类型列表失败。")

    def get_space_type_by_id(self, user: CustomUser, pk: int) -> ServiceResult[SpaceType]:
        """
        根据ID获取单个空间类型详情。
        只允许系统管理员和超级管理员操作。(与 SpaceTypeAdmin 的 has_view_permission 保持一致)
        """
        try:
            space_type = self.space_type_dao.get_by_id(pk)
            if not space_type:
                return ServiceResult.error_result(
                    message="空间类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # 简化权限检查：user.is_system_admin 已包含 user.is_superuser
            if not user.is_system_admin:  # <--- 修改点
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
        创建新的空间类型。
        只允许系统管理员和超级管理员操作。(与 SpaceTypeAdmin 的 has_add_permission 保持一致)
        """
        # 简化权限检查：user.is_system_admin 已包含 user.is_superuser
        if not user.is_system_admin:  # <--- 修改点
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
        更新空间类型。
        只允许系统管理员和超级管理员操作。(与 SpaceTypeAdmin 的 has_change_permission 保持一致)
        """
        # 简化权限检查：user.is_system_admin 已包含 user.is_superuser
        if not user.is_system_admin:  # <--- 修改点
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
        删除空间类型。
        只允许系统管理员和超级管理员操作。(与 SpaceTypeAdmin 的 has_delete_permission 保持一致)
        """
        # 简化权限检查：user.is_system_admin 已包含 user.is_superuser
        if not user.is_system_admin:  # <--- 修改点
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