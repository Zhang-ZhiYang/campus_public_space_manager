# spaces/service/space_service.py - START OF FILE
import logging
import hashlib
import json
from typing import List, Dict, Any, Optional
from django.db.models import QuerySet, Q
from django.db import transaction
from guardian.shortcuts import assign_perm, remove_perm

from core.service import BaseService, ServiceResult
from core.dao import DAOFactory
from core.service.cache import CacheService
from core.utils.exceptions import NotFoundException, BadRequestException, \
    ForbiddenException, CustomAPIException, InternalServerError
# 导入 Space, BookableAmenity, Amenity 和 SPACE_MANAGEMENT_PERMISSIONS
from spaces.models import Space, BookableAmenity, Amenity, SPACE_MANAGEMENT_PERMISSIONS
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
                                 'parent_space', 'check_in_by']
    _allowed_select_related = ['space_type', 'managed_by', 'parent_space']

    def __init__(self):
        super().__init__()
        self._space_dao = DAOFactory.get_dao('space')
        self._spacetype_dao = DAOFactory.get_dao('space_type')
        self._amenity_dao = DAOFactory.get_dao('amenity')
        self._bookableamenity_dao = DAOFactory.get_dao('bookable_amenity')

    def get_all_spaces(self, user: CustomUser) -> ServiceResult[QuerySet[Space]]:
        logger.debug(f"Service: 获取所有空间 QuerySet (User: {user.username}).")
        try:
            db_queryset = self._space_dao.get_all_spaces(
                user=user,
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )
            return ServiceResult.success_result(data=db_queryset)
        except Exception as e:
            logger.exception(f"获取所有空间失败 (User: {user.username}).")
            return self._handle_exception(e, default_message="获取所有空间失败。", default_status_code=500)

    def get_managed_spaces(self, user: CustomUser) -> ServiceResult[QuerySet[Space]]:
        """
        获取用户有权限管理（managed_by=user）的空间列表，或如果是系统管理员，则返回所有有 manager 的空间。
        """
        if not (user.is_space_manager or user.is_system_admin):
            raise ForbiddenException(detail="您没有权限查看管理空间列表。")

        logger.debug(f"Service: 获取用户管理的 Space QuerySet (ManagerUser: {user.username}).")
        try:
            db_queryset = self._space_dao.get_managed_spaces(
                user=user,
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )
            return ServiceResult.success_result(data=db_queryset)
        except Exception as e:
            logger.exception(f"获取用户管理空间失败 (User: {user.username}).")
            return self._handle_exception(e, default_message="获取用户管理空间失败。", default_status_code=500)

    @CacheService.cache_method(key_prefix='spaces:space', identifier_arg='pk', user_arg_name='user')
    def get_space_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Dict[str, Any]]:
        try:
            space = self._space_dao.get_space_by_id(
                user=user,
                pk=pk,
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )

            if not space:
                raise NotFoundException(
                    detail=f"Space with ID {pk} not found or you do not have permission to view it.")

            space_dict = space.to_dict(include_related=True)
            return ServiceResult.success_result(data=space_dict)
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"获取空间详情失败 (ID: {pk}, User: {user.username}).")
            return self._handle_exception(e, default_message="获取空间详情失败。", default_status_code=500)

    @transaction.atomic
    def create_space(self, user: CustomUser,
                     space_data: Dict[str, Any],
                     permitted_groups_data: Optional[List[Group]] = None,
                     amenities_data: Optional[List[Dict[str, Any]]] = None,
                     check_in_by_data: Optional[List[CustomUser]] = None,
                     managed_by_data: Optional[CustomUser] = None
                     ) -> ServiceResult[Dict[str, Any]]:
        # --- 权限校验 ---
        if not (user.is_system_admin or user.is_space_manager):
            raise ForbiddenException(detail="您没有权限创建空间。")

        actual_managed_by = space_data.get('managed_by', managed_by_data)
        new_parent_space = space_data.get('parent_space')

        if user.is_space_manager and not user.is_system_admin:
            if actual_managed_by and actual_managed_by != user:
                raise ForbiddenException(detail="您没有权限指定其他用户为您创建的空间的主要管理人员。")
            if not actual_managed_by:
                space_data['managed_by'] = user

            if new_parent_space:
                parent_space_actual = self._space_dao.get_by_id(new_parent_space.pk)
                if not parent_space_actual or parent_space_actual.managed_by != user:
                    raise ForbiddenException(
                        f"您没有权限在未由您管理的空间 '{parent_space_actual.name if parent_space_actual else new_parent_space.pk}' 下创建子空间。")
        # --- 权限校验 END ---

        try:
            new_space = self._space_dao.create(**space_data)

            if new_space.managed_by:
                # 正确访问 SPACE_MANAGEMENT_PERMISSIONS
                for perm_codename in SPACE_MANAGEMENT_PERMISSIONS:
                    assign_perm(f'spaces.{perm_codename}', new_space.managed_by, new_space)

                space_manager_group, _ = Group.objects.get_or_create(name='空间管理员')
                if not new_space.managed_by.groups.filter(name='空间管理员').exists():
                    new_space.managed_by.groups.add(space_manager_group)

            if permitted_groups_data is not None:
                new_space.permitted_groups.set(permitted_groups_data)

            if amenities_data is not None:
                for amenity_item in amenities_data:
                    amenity_id = amenity_item['amenity_id']
                    quantity = amenity_item.get('quantity', 1)
                    is_bookable = amenity_item.get('is_bookable', True)
                    is_active = amenity_item.get('is_active', True)

                    amenity = self._amenity_dao.get_by_id(amenity_id)
                    if not amenity:
                        raise BadRequestException(f"设施类型ID {amenity_id} 未找到。")

                    self._bookableamenity_dao.create(
                        space=new_space,
                        amenity=amenity,
                        quantity=quantity,
                        is_bookable=is_bookable,
                        is_active=is_active
                    )
            logger.debug(f"Added bookable amenities for new space {new_space.id}.")

            if check_in_by_data is not None:
                new_space.check_in_by.set(check_in_by_data)

            return ServiceResult.success_result(
                data=new_space.to_dict(),
                message="空间创建成功。",
                status_code=201
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"创建空间失败 (用户: {user.username}, 数据: {space_data})。")
            return self._handle_exception(e, default_message="创建空间失败。", default_status_code=500)

    @transaction.atomic
    def update_space(self, user: CustomUser, pk: int,
                     space_data: Dict[str, Any],
                     permitted_groups_data: Optional[List[Group]] = None,
                     amenities_data: Optional[List[Dict[str, Any]]] = None,
                     check_in_by_data: Optional[List[CustomUser]] = None,
                     managed_by_data: Optional[CustomUser] = None
                     ) -> ServiceResult[Dict[str, Any]]:
        try:
            space = self._space_dao.get_by_id(pk)
            if not space:
                raise NotFoundException(detail="空间未找到。")

            # --- 权限校验 ---
            is_system_admin = user.is_system_admin

            if user.is_space_manager and not is_system_admin and space.managed_by != user:
                raise ForbiddenException(detail=f"您没有权限修改非您管理的空间 '{space.name}'。")

            new_managed_by_from_data = space_data.get('managed_by')
            if new_managed_by_from_data is not None:
                new_managed_by = new_managed_by_from_data
            elif managed_by_data is not None:
                new_managed_by = managed_by_data
            else:
                new_managed_by = space.managed_by

            old_managed_by = space.managed_by

            if ('managed_by' in space_data or managed_by_data is not None) and new_managed_by != old_managed_by:
                if not (is_system_admin or user.has_perm('spaces.can_assign_space_manager', space)):
                    raise ForbiddenException("您没有权限分配空间管理人员。")
                if user.is_space_manager and not is_system_admin:
                    if new_managed_by and new_managed_by != user:
                        raise ForbiddenException(detail="空间管理员只能将自己的空间分配给自己。")
                    if old_managed_by == user and new_managed_by != user and new_managed_by is not None:
                        raise ForbiddenException(detail="空间管理员只能将自己管理的空间的管理人改为自己或清空。")

            if 'name' in space_data or 'location' in space_data or 'description' in space_data or \
                    'capacity' in space_data or 'image' in space_data or 'parent_space' in space_data:
                if not (is_system_admin or user.has_perm('spaces.can_edit_space_info', space)):
                    raise ForbiddenException("您没有权限编辑空间基本信息。")

            if 'is_active' in space_data or 'is_bookable' in space_data or 'is_container' in space_data:
                if not (is_system_admin or user.has_perm('spaces.can_change_space_status', space)):
                    raise ForbiddenException("您没有权限更改空间状态。")

            if 'requires_approval' in space_data or 'available_start_time' in space_data or \
                    'available_end_time' in space_data or 'min_booking_duration' in space_data or \
                    'max_booking_duration' in space_data or 'buffer_time_minutes' in space_data:
                if not (is_system_admin or user.has_perm('spaces.can_configure_booking_rules', space)):
                    raise ForbiddenException("您没有权限配置预订规则。")

            if permitted_groups_data is not None and set(p.pk for p in permitted_groups_data) != set(
                    group.pk for group in space.permitted_groups.all()):
                if not (is_system_admin or user.has_perm('spaces.can_manage_permitted_groups', space)):
                    raise ForbiddenException("您没有权限修改空间的许可访问组。")

            if check_in_by_data is not None and set(u.pk for u in check_in_by_data) != set(
                    user.pk for user in space.check_in_by.all()):
                if not (is_system_admin): # 仅系统管理员可以修改可签到人员
                    raise ForbiddenException("您没有权限修改空间的可签到人员。")

            if amenities_data is not None:
                if not (is_system_admin or (user.is_space_manager and space.managed_by == user)):
                    raise ForbiddenException(detail=f"您没有权限管理空间 '{space.name}' 的设施。")
            # --- 权限校验 END ---

            updated_space = self._space_dao.update(space, **space_data)

            if permitted_groups_data is not None:
                updated_space.permitted_groups.set(permitted_groups_data)

            if amenities_data is not None:
                space.bookable_amenities.all().delete()
                logger.debug(f"Deleted all existing bookable amenities for space {space.id}.")

                for amenity_item in amenities_data:
                    amenity_id = amenity_item['amenity_id']
                    quantity = amenity_item.get('quantity', 1)
                    is_bookable = amenity_item.get('is_bookable', True)
                    is_active = amenity_item.get('is_active', True)

                    amenity = self._amenity_dao.get_by_id(amenity_id)
                    if not amenity:
                        raise BadRequestException(f"设施类型ID {amenity_id} 未找到。")

                    self._bookableamenity_dao.create(
                        space=updated_space,
                        amenity=amenity,
                        quantity=quantity,
                        is_bookable=is_bookable,
                        is_active=is_active
                    )
                logger.debug(f"Recreated bookable amenities for space {updated_space.id}.")

            if check_in_by_data is not None:
                updated_space.check_in_by.set(check_in_by_data)

            return ServiceResult.success_result(
                data=updated_space.to_dict(),
                message="空间更新成功。",
                status_code=200
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新空间失败 (ID: {pk}, 用户: {user.username}, 数据: {space_data})。")
            return self._handle_exception(e, default_message="更新空间失败。", default_status_code=500)

    @transaction.atomic
    def delete_space(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        try:
            space = self._space_dao.get_by_id(pk)
            if not space:
                raise NotFoundException(detail="空间未找到。")

            # --- 权限校验 ---
            is_system_admin = user.is_system_admin

            if user.is_space_manager and not is_system_admin and space.managed_by != user:
                raise ForbiddenException(detail="您没有权限删除此空间。")

            if not (is_system_admin or user.has_perm('spaces.can_delete_space', space)):
                raise ForbiddenException(detail="您没有权限删除此空间。")
            # --- 权限校验 END ---

            if space.check_in_by.exists():
                for staff_user in space.check_in_by.all():
                    remove_perm('can_check_in_real_space', staff_user, space)

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
            return self._handle_exception(e, default_message="删除空间失败。", default_status_code=500)

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