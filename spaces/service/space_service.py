# spaces/service/space_service.py
import logging
import hashlib
import json
from typing import List, Dict, Any, Union, Optional
from django.db.models import QuerySet, Q
from django.db import transaction

from core.service import BaseService, ServiceResult
from core.dao import DAOFactory
from core.cache import CacheService
from core.utils.exceptions import ServiceException, NotFoundException, BadRequestException, \
    ForbiddenException, CustomAPIException
from spaces.models import Space, SpaceType, Amenity, BookableAmenity
from users.models import CustomUser
from django.contrib.auth.models import Group

logger = logging.getLogger(__name__)


class SpaceService(BaseService):
    _dao_map = {
        'space_dao': 'space',
        'amenity_dao': 'amenity',
        'bookable_amenity_dao': 'bookable_amenity',
        'space_type_dao': 'space_type'
    }
    _allowed_prefetch_related = ['space_type', 'managed_by', 'bookable_amenities__amenity', 'permitted_groups',
                                 'parent_space']
    _allowed_select_related = ['space_type', 'managed_by', 'parent_space']

    def __init__(self):
        super().__init__()
        self._space_dao = DAOFactory.get_dao('space')
        self._spacetype_dao = DAOFactory.get_dao('space_type')
        self._amenity_dao = DAOFactory.get_dao('amenity')
        self._bookableamenity_dao = DAOFactory.get_dao('bookable_amenity')

    def get_all_spaces(self, user: CustomUser) -> ServiceResult[QuerySet[Space]]:
        logger.debug(f"Service: 获取所有空间 QuerySet (User: {user.username}).")

        db_queryset = self._space_dao.get_all_spaces(
            user=user,
            prefetch_related=self._allowed_prefetch_related,
            select_related=self._allowed_select_related
        )

        return ServiceResult.success_result(data=db_queryset)

    @CacheService.cache_method(key_prefix='spaces:space')
    def get_space_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Dict[str, Any]]:
        space = self._space_dao.get_space_by_id(
            user=user,
            pk=pk,
            prefetch_related=self._allowed_prefetch_related,
            select_related=self._allowed_select_related
        )

        if not space:
            raise NotFoundException(detail=f"Space with ID {pk} not found or you do not have permission to view it.")

        space_dict = space.to_dict(include_related=True)
        return ServiceResult.success_result(data=space_dict)

    @transaction.atomic
    def create_space(self, user: CustomUser,
                     space_data: Dict[str, Any],
                     permitted_groups_data: Optional[List[Group]] = None,
                     amenity_ids_data: Optional[List[int]] = None
                     ) -> ServiceResult[Dict[str, Any]]:
        try:
            new_space = self._space_dao.create(**space_data)

            if permitted_groups_data is not None:
                new_space.permitted_groups.set(permitted_groups_data)

            if amenity_ids_data is not None:
                self._update_space_amenities(new_space, amenity_ids_data, user)

            return ServiceResult.success_result(
                data=new_space.to_dict(),
                message="空间创建成功。",
                status_code=201
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"创建空间失败 (用户: {user.username}, 数据: {space_data})。")
            return self._handle_exception(e, default_message="创建空间失败。")

    @transaction.atomic
    def update_space(self, user: CustomUser, pk: int,
                     space_data: Dict[str, Any],
                     permitted_groups_data: Optional[List[Group]] = None,
                     amenity_ids_data: Optional[List[int]] = None
                     ) -> ServiceResult[Dict[str, Any]]:
        try:
            space = self._space_dao.get_by_id(pk)
            if not space:
                raise NotFoundException(detail="空间未找到。")

            is_system_admin = getattr(user, 'is_system_admin', False) or user.is_superuser
            is_space_manager_group_member = user.is_staff and user.groups.filter(name='空间管理员').exists()

            if 'space_type' in space_data and space_data['space_type'] != space.space_type:
                if not is_system_admin:
                    raise ForbiddenException(f"您没有权限更改空间类型。")

            new_manager_target = space_data.get('managed_by')
            new_manager_pk = new_manager_target.pk if new_manager_target else None
            old_manager_pk = space.managed_by.pk if space.managed_by else None

            if 'managed_by' in space_data and new_manager_pk != old_manager_pk:
                if not (is_system_admin or user.has_perm('spaces.can_assign_space_manager', space)):
                    raise ForbiddenException(f"您没有权限分配空间管理人员。")

            editable_info_fields = ['name', 'location', 'description', 'capacity', 'image', 'parent_space']
            if any(f in space_data for f in editable_info_fields) and not (
                    is_system_admin or is_space_manager_group_member or user.has_perm('spaces.can_edit_space_info',
                                                                                      space)):
                raise ForbiddenException(f"您没有权限编辑空间基本信息。")

            status_fields = ['is_active', 'is_bookable', 'is_container']
            if any(f in space_data for f in status_fields) and not (
                    is_system_admin or is_space_manager_group_member or user.has_perm('spaces.can_change_space_status',
                                                                                      space)):
                raise ForbiddenException(f"您没有权限更改空间状态。")

            booking_rule_fields = ['requires_approval', 'available_start_time', 'available_end_time',
                                   'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes']
            if any(f in space_data for f in booking_rule_fields) and not (
                    is_system_admin or is_space_manager_group_member or user.has_perm(
                'spaces.can_configure_booking_rules', space)):
                raise ForbiddenException(f"您没有权限配置预订规则。")

            if permitted_groups_data is not None:
                if not (is_system_admin or user.has_perm('spaces.can_change_permitted_groups', space)):
                    raise ForbiddenException("您没有权限修改空间的许可访问组。")

            updated_space = self._space_dao.update(space, **space_data)

            if permitted_groups_data is not None:
                updated_space.permitted_groups.set(permitted_groups_data)

            if amenity_ids_data is not None:
                self._update_space_amenities(updated_space, amenity_ids_data, user)

            return ServiceResult.success_result(
                data=updated_space.to_dict(),
                message="空间更新成功。",
                status_code=200
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新空间失败 (ID: {pk}, 用户: {user.username}, 数据: {space_data})。")
            return self._handle_exception(e, default_message="更新空间失败。")

    @transaction.atomic
    def delete_space(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        try:
            space = self._space_dao.get_by_id(pk)
            if not space:
                raise NotFoundException(detail="空间未找到。")

            is_system_admin = getattr(user, 'is_system_admin', False) or user.is_superuser
            if not (is_system_admin or user.has_perm('spaces.can_delete_space', space)):
                raise ForbiddenException(detail="您没有权限删除此空间。")

            if space.child_spaces.exists():
                raise BadRequestException(detail="存在子空间，无法删除此空间。请先删除或解除所有子空间与此空间的关联。")

            from bookings.models import Booking
            if Booking.objects.filter(Q(space=space) | Q(bookable_amenity__space=space)).exists():
                raise BadRequestException(detail="存在关联的预订记录，无法删除此空间。请先处理所有预订记录。")

            self._space_dao.delete(space)

            return ServiceResult.success_result(
                message="空间删除成功。",
                status_code=204
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"删除空间失败 (ID: {pk}, 用户: {user.username})。")
            return self._handle_exception(e, default_message="删除空间失败。")

    @transaction.atomic
    def _update_space_amenities(self, space: Space, amenity_ids: Optional[List[int]], user: CustomUser):
        if amenity_ids is None:
            return

        existing_ba_map = {
            ba.amenity_id: ba for ba in self._bookableamenity_dao.get_bookable_amenities_for_space(space.pk, user)
        }

        new_amenity_ids_set = set(amenity_ids)
        current_amenity_ids_set = set(existing_ba_map.keys())

        is_system_admin = getattr(user, 'is_system_admin', False) or user.is_superuser

        amenities_to_add = new_amenity_ids_set - current_amenity_ids_set
        for amenity_id in amenities_to_add:
            amenity_obj = self._amenity_dao.get_by_id(amenity_id)
            if not amenity_obj:
                raise BadRequestException(f"设施类型ID {amenity_id} 未找到。")

            if not (is_system_admin or user.has_perm('spaces.can_add_space_amenity', space)):
                raise ForbiddenException(f"您没有权限向空间 '{space.name}' 添加设施类型 '{amenity_obj.name}'。")

            self._bookableamenity_dao.create(
                space=space,
                amenity=amenity_obj,
                quantity=1,
                is_bookable=amenity_obj.is_bookable_individually,
                is_active=True
            )
            logger.debug(f"Added new bookable amenity {amenity_obj.name} to space {space.id}.")

        amenities_to_remove = current_amenity_ids_set - new_amenity_ids_set
        for amenity_id in amenities_to_remove:
            ba_to_remove = existing_ba_map[amenity_id]

            if not (is_system_admin or user.has_perm('spaces.can_delete_bookable_amenity',
                                                     ba_to_remove) or user.has_perm('spaces.can_remove_space_amenity',
                                                                                    space)):
                raise ForbiddenException(
                    f"您没有权限从空间 '{space.name}' 移除设施实例 '{ba_to_remove.amenity.name}' (PK: {ba_to_remove.id})。")

            from bookings.models import Booking as BookingModelForCheck
            if BookingModelForCheck.objects.filter(bookable_amenity=ba_to_remove).exists():
                raise BadRequestException(f"设施实例 '{ba_to_remove.amenity.name}' 存在关联的预订记录，无法移除。")

            self._bookableamenity_dao.delete(ba_to_remove)
            logger.debug(
                f"Removed bookable amenity {ba_to_remove.amenity.name} (PK:{ba_to_remove.pk}) from space {space.id}.")

    # Removed _get_user_role_postfix as it's no longer directly part of Space list cache key.
    # User roles are handled by filtering in get_all_spaces and view permissions.

    def _get_request_query_params_hash(self, request_query_params: Dict[str, Any]) -> str:
        if not request_query_params:
            return 'not_specified'
        sorted_params = dict(sorted(request_query_params.items()))
        params_string = json.dumps(sorted_params, sort_keys=True)
        return hashlib.md5(params_string.encode('utf-8')).hexdigest()

    def get_dynamic_list_cache_key_parts(self, request_query_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        为动态列表提供 CacheService.get_list_cache 和 set_list_cache 所需的 kwargs。
        现在只包含查询参数的哈希。
        """
        return {
            'query_params_hash': self._get_request_query_params_hash(request_query_params)
        }