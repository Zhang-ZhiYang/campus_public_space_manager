# spaces/service/space_service.py
import logging
from typing import List, Dict, Any
from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import QuerySet

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, \
    ConflictException  # 导入 ConflictException
from spaces.models import Space, Amenity, BookableAmenity
from django.contrib.auth import get_user_model
from guardian.shortcuts import get_objects_for_user, assign_perm

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


class SpaceService(BaseService):
    _dao_map = {
        'space_dao': 'space',
        'amenity_dao': 'amenity',
        'bookable_amenity_dao': 'bookable_amenity',
    }

    def get_all_spaces(self, user: CustomUser) -> ServiceResult[QuerySet[Space]]:
        """
        获取所有空间列表。对于普通用户，只返回可预订且活跃的空间。
        对于管理员，返回他们有权限管理的所有空间。
        """
        try:
            if user.is_superuser or getattr(user, 'is_system_admin', False):
                spaces_qs = self.space_dao.get_queryset()
            elif user.is_authenticated and getattr(user, 'is_space_manager', False):
                spaces_qs = self.space_dao.get_spaces_for_user_management(user)
            else:
                spaces_qs = self.space_dao.get_queryset().filter(is_active=True, is_bookable=True)

            return ServiceResult.success_result(
                data=spaces_qs,
                message="成功获取空间列表。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="获取空间列表失败。")

    def get_space_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Space]:
        """
        根据ID获取单个空间详情。
        权限：超级管理员/系统管理员可以看所有。空间管理员可以看他们有权限管理的空间。普通用户可以看活跃且可预订的空间。
        """
        try:
            space = self.space_dao.get_by_id(pk)
            if not space:
                return ServiceResult.error_result(
                    message="空间未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            can_view = False
            if user.is_superuser or getattr(user, 'is_system_admin', False):
                can_view = True
            elif user.is_authenticated and getattr(user, 'is_space_manager', False):
                can_view = user.has_perm('spaces.can_manage_space_details', space)
            elif user.is_authenticated:
                can_view = space.is_active and space.is_bookable

            if not can_view:
                return ServiceResult.error_result(  # <--- 修改这里
                    message=ForbiddenException.default_detail,
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

            return ServiceResult.success_result(
                data=space,
                message="成功获取空间详情。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="获取空间详情失败。")

    @transaction.atomic
    def create_space(self, user: CustomUser, space_data: Dict[str, Any]) -> ServiceResult[Space]:
        """
        创建新的空间。只有系统管理员或超级管理员可以操作。
        """
        if not (user.is_superuser or getattr(user, 'is_system_admin', False)):
            return ServiceResult.error_result(  # <--- 修改这里
                message=ForbiddenException.default_detail,
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )

        amenity_ids = space_data.pop('amenity_ids', [])
        managed_by_id = space_data.pop('managed_by_id', None)

        try:
            if managed_by_id:
                try:
                    managed_by_user = CustomUser.objects.get(pk=managed_by_id)
                    space_data['managed_by'] = managed_by_user
                except CustomUser.DoesNotExist:
                    return ServiceResult.error_result(  # <--- 修改这里
                        message=f"管理人员ID {managed_by_id} 不存在。",
                        error_code=BadRequestException.default_code,
                        status_code=BadRequestException.status_code
                    )

            new_space = self.space_dao.create(**space_data)

            self._update_space_amenities(new_space, amenity_ids)

            return ServiceResult.success_result(
                data=new_space,
                message="空间创建成功。",
                status_code=201
            )
        except Exception as e:
            return self._handle_exception(e, default_message="创建空间失败。")

    @transaction.atomic
    def update_space(self, user: CustomUser, pk: int, space_data: Dict[str, Any]) -> ServiceResult[Space]:
        """
        更新空间。只有系统管理员、超级管理员或有权限的空间管理员可以操作。
        """
        try:
            space = self.space_dao.get_by_id(pk)
            if not space:
                return ServiceResult.error_result(
                    message="空间未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            if not (user.is_superuser or getattr(user, 'is_system_admin', False) or
                    user.has_perm('spaces.can_manage_space_details', space)):
                return ServiceResult.error_result(  # <--- 修改这里
                    message=ForbiddenException.default_detail,
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

            amenity_ids = space_data.pop('amenity_ids', None)
            managed_by_id = space_data.pop('managed_by_id', False)

            if managed_by_id is not False:
                if managed_by_id is None:
                    space_data['managed_by'] = None
                else:
                    try:
                        managed_by_user = CustomUser.objects.get(pk=managed_by_id)
                        space_data['managed_by'] = managed_by_user
                    except CustomUser.DoesNotExist:
                        return ServiceResult.error_result(  # <--- 修改这里
                            message=f"管理人员ID {managed_by_id} 不存在。",
                            error_code=BadRequestException.default_code,
                            status_code=BadRequestException.status_code
                        )

            updated_space = self.space_dao.update(space, **space_data)

            if amenity_ids is not None:
                self._update_space_amenities(updated_space, amenity_ids)

            return ServiceResult.success_result(
                data=updated_space,
                message="空间更新成功。",
                status_code=200
            )
        except Exception as e:
            return self._handle_exception(e, default_message="更新空间失败。")

    @transaction.atomic
    def delete_space(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        """
        删除空间。只有系统管理员、超级管理员或有权限的空间管理员可以操作。
        需要检查是否有子空间或预订记录关联。
        """
        try:
            space = self.space_dao.get_by_id(pk)
            if not space:
                return ServiceResult.error_result(
                    message="空间未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            if not (user.is_superuser or getattr(user, 'is_system_admin', False) or
                    user.has_perm('spaces.can_manage_space_details', space)):
                return ServiceResult.error_result(  # <--- 修改这里
                    message=ForbiddenException.default_detail,
                    error_code=ForbiddenException.default_code,
                    status_code=ForbiddenException.status_code
                )

            from bookings.models import Booking

            if self.space_dao.space_has_children(space):
                return ServiceResult.error_result(
                    message="存在子空间，无法删除此空间。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code,
                    errors=["请先删除或解除所有子空间与此空间的关联。"]
                )

            if self.space_dao.space_has_bookings(space, Booking):
                return ServiceResult.error_result(
                    message="存在关联的预订记录，无法删除此空间。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code,
                    errors=["请先处理所有预订记录。"]
                )

            bookable_amenities = self.bookable_amenity_dao.get_bookable_amenities_for_space(space)
            for ba in bookable_amenities:
                if ba.amenity_bookings.exists():
                    return ServiceResult.error_result(
                        message=f"该空间下的设施 '{ba.amenity.name}' 存在关联的预订记录，无法删除此空间。",
                        error_code=BadRequestException.default_code,
                        status_code=BadRequestException.status_code,
                        errors=["请先处理所有相关设施的预订记录。"]
                    )

            self.space_dao.delete(space)
            return ServiceResult.success_result(
                message="空间删除成功。",
                status_code=204
            )
        except Exception as e:
            return self._handle_exception(e, default_message="删除空间失败。")

    @transaction.atomic
    def _update_space_amenities(self, space: Space, amenity_ids: List[int]):
        """
        内部辅助方法：为空间更新其关联的 BookableAmenity 实例。
        """
        existing_amenity_map = {
            ba.amenity_id: ba for ba in self.bookable_amenity_dao.get_bookable_amenities_for_space(space)
        }

        amenities_to_add = [aid for aid in amenity_ids if aid not in existing_amenity_map]

        amenities_to_remove = [
            ba for ba in existing_amenity_map.values()
            if ba.amenity_id not in amenity_ids
        ]

        if amenities_to_add:
            amenity_objects = self.amenity_dao.filter(pk__in=amenities_to_add)
            new_bookable_amenities = []
            for amenity_obj in amenity_objects:
                new_bookable_amenities.append(
                    BookableAmenity(space=space, amenity=amenity_obj, quantity=1,
                                    is_bookable=amenity_obj.is_bookable_individually)
                )
            if new_bookable_amenities:
                self.bookable_amenity_dao.bulk_create(new_bookable_amenities)
            logger.info(f"Added {len(new_bookable_amenities)} new bookable amenities to space {space.pk}.")

        if amenities_to_remove:
            self.bookable_amenity_dao._manager.filter(pk__in=[ba.pk for ba in amenities_to_remove]).delete()
            logger.info(f"Removed {len(amenities_to_remove)} bookable amenities from space {space.pk}.")