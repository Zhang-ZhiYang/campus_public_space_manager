# spaces/service/space_type_service.py
import logging
from typing import List
from django.db import transaction
from django.db.models import QuerySet

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException
from spaces.models import SpaceType
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
CustomUser = get_user_model()

class SpaceTypeService(BaseService):
    _dao_map = {
        'space_type_dao': 'space_type',
    }

    def get_all_space_types(self, user: CustomUser) -> ServiceResult[QuerySet[SpaceType]]:
        """
        获取所有空间类型。视图层确保用户已认证。
        所有认证用户可见所有空间类型列表。
        """
        try:
            # 移除了所有 is_xxx 权限检查，现在由视图层的装饰器处理，并假设基础认证已完成
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
        根据ID获取单个空间类型详情。视图层确保用户已认证。
        所有认证用户可见空间类型详情。
        """
        try:
            # 移除了所有 is_xxx 权限检查，现在由视图层的装饰器处理，并假设基础认证已完成
            space_type = self.space_type_dao.get_by_id(pk)
            if not space_type:
                return ServiceResult.error_result(
                    message="空间类型未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
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
        权限已在视图层通过装饰器检查。
        """
        # 移除了所有的 is_xxx 权限检查，现在由视图层的装饰器处理
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
        权限已在视图层通过装饰器检查。
        """
        # 移除了所有的 is_xxx 权限检查，现在由视图层的装饰器处理
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
        权限已在视图层通过装饰器检查。
        """
        # 移除了所有的 is_xxx 权限检查，现在由视图层的装饰器处理
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
            return self._handle_exception(e, default_message="删除设施类型失败。")