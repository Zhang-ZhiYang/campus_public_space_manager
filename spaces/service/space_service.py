# spaces/service/space_service.py - START OF FILE
import logging
import hashlib
import json
from typing import List, Dict, Any, Optional
from django.db.models import QuerySet, Q
from django.db import transaction
from guardian.shortcuts import assign_perm, remove_perm # 确保导入这些

from core.service import BaseService, ServiceResult
from core.dao import DAOFactory
from core.service.cache import CacheService
from core.utils.exceptions import NotFoundException, BadRequestException, \
    ForbiddenException, CustomAPIException, InternalServerError # 确保 InternalServerError 被导入
from spaces.models import Space, BookableAmenity, Amenity # Amenity 也要导入以便 _update_space_amenities
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
                                 'parent_space', 'check_in_by'] # NEW: 添加 check_in_by
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

    # --- 新增的方法：获取用户管理的或系统管理员可见的空间 ---
    def get_managed_spaces(self, user: CustomUser) -> ServiceResult[QuerySet[Space]]:
        """
        获取用户有权限管理（managed_by=user）的空间列表，或如果是系统管理员，则返回所有有 manager 的空间。
        """
        if not (user.is_space_manager or user.is_system_admin):
            raise ForbiddenException(detail="您没有权限查看管理空间列表。")

        logger.debug(f"Service: 获取用户管理的 Space QuerySet (ManagerUser: {user.username}).")
        try:
            db_queryset = self._space_dao.get_managed_spaces(
                user=user,  # DAO层将根据用户的 is_system_admin 或 managed_by 属性进行过滤
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )
            return ServiceResult.success_result(data=db_queryset)
        except Exception as e:
            logger.exception(f"获取用户管理空间失败 (User: {user.username}).")
            return self._handle_exception(e, default_message="获取用户管理空间失败。", default_status_code=500)

    # --- 新增的方法 END ---

    @CacheService.cache_method(key_prefix='spaces:space', identifier_arg='pk', user_arg_name='user')
    def get_space_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Dict[str, Any]]:
        try:
            space = self._space_dao.get_space_by_id(  # _space_dao 的 get_space_by_id 已经包含了权限过滤
                user=user,
                pk=pk,
                prefetch_related=self._allowed_prefetch_related,
                select_related=self._allowed_select_related
            )

            if not space:
                # 区分未找到和无权限，以便返回更准确的错误提示
                if user.is_system_admin or user.is_space_manager or user.is_check_in_staff: # NEW: 签到员也可以查看他能签到的空间
                    # 尝试以非权限方式获取一次，如果存在但仍无法获取，则说明是权限问题
                    # 但在这里假设 DAO 已经做了最细粒度的权限过滤
                    # 如果 DAO.get_space_by_id 返回 None，那就是真的找不到或没权限
                    raise NotFoundException(
                        detail=f"Space with ID {pk} not found or you do not have permission to view it.")
                else:  # 普通用户
                    raise NotFoundException(detail=f"Space with ID {pk} not found.")

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
                     amenity_ids_data: Optional[List[int]] = None,
                     check_in_by_data: Optional[List[CustomUser]] = None # NEW: 添加 check_in_by_data
                     ) -> ServiceResult[Dict[str, Any]]:
        # --- 权限校验 ---
        if not (user.is_system_admin or user.is_space_manager):
            raise ForbiddenException(detail="您没有权限创建空间。")

        new_managed_by = space_data.get('managed_by')  # `managed_by` 是 CustomUser 实例
        new_parent_space = space_data.get('parent_space')  # `parent_space` 是 Space 实例

        if user.is_space_manager and not user.is_system_admin:
            # 空间管理员创建空间时，如果指定 managed_by，必须是自己
            if new_managed_by and new_managed_by != user:
                raise ForbiddenException(detail="您没有权限指定其他用户为您创建的空间的主要管理人员。")
            # 如果未指定 managed_by，则自动设置为当前空间管理员
            if not new_managed_by:
                space_data['managed_by'] = user

            # 空间管理员创建子空间时，父空间必须是自己管理的
            if new_parent_space:
                parent_space_actual = self._space_dao.get_by_id(new_parent_space.pk)
                # 检查父空间是否存在且其 managed_by 是当前用户
                if not parent_space_actual or parent_space_actual.managed_by != user:
                    raise ForbiddenException(
                        f"您没有权限在未由您管理的空间 '{parent_space_actual.name if parent_space_actual else new_parent_space.pk}' 下创建子空间。")
        # --- 权限校验 END ---

        try:
            new_space = self._space_dao.create(**space_data)

            # 权限分配逻辑 (如果 new_space 有 managed_by)
            if new_space.managed_by:
                # 给 new_space.managed_by 分配管理此空间的所有权限
                for perm_codename in Space.SPACE_MANAGEMENT_PERMISSIONS:
                    assign_perm(f'spaces.{perm_codename}', new_space.managed_by, new_space)

                # 确保管理人员在“空间管理员”组
                space_manager_group, _ = Group.objects.get_or_create(name='空间管理员')
                if not new_space.managed_by.groups.filter(name='空间管理员').exists():
                    new_space.managed_by.groups.add(space_manager_group)

            if permitted_groups_data is not None:
                new_space.permitted_groups.set(permitted_groups_data)

            if amenity_ids_data is not None:
                self._update_space_amenities(new_space, amenity_ids_data, user) # _update_space_amenities 内部有权限检查

            # NEW: 处理 check_in_by 字段及其权限
            if check_in_by_data is not None:
                new_space.check_in_by.set(check_in_by_data) # 直接设置 ManyToMany 关系
                # Space.save() 方法中已包含对 check_in_by 权限的分配和组管理，无需在此处重复

            # 缓存失效将在信号处理中完成，避免在此处重复调用
            # CacheService.invalidate_all_related_cache('spaces:space')
            # CacheService.invalidate_object_cache('spaces:space', new_space.pk)

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
                     amenity_ids_data: Optional[List[int]] = None,
                     check_in_by_data: Optional[List[CustomUser]] = None # NEW: 添加 check_in_by_data
                     ) -> ServiceResult[Dict[str, Any]]:
        try:
            # 获取原始 Space 实例
            # 这里我们使用 get_by_id 而不是 get_space_by_id，因为权限检查应该对更新能力进行，而不是仅查看能力。
            # 实际的权限在下面的逻辑中进行检查。
            space = self._space_dao.get_by_id(pk)
            if not space:
                raise NotFoundException(detail="空间未找到。")

            # --- 权限校验 ---
            is_system_admin = user.is_system_admin

            # 对于非系统管理员的空间管理员，只能操作自己管理的空间
            if user.is_space_manager and not is_system_admin and space.managed_by != user:
                raise ForbiddenException(detail=f"您没有权限修改非您管理的空间 '{space.name}'。")

            # 检查是否有 'can_assign_space_manager' 权限
            new_managed_by = space_data.get('managed_by')
            old_managed_by = space.managed_by

            if 'managed_by' in space_data and new_managed_by != old_managed_by:
                if not (is_system_admin or user.has_perm('spaces.can_assign_space_manager', space)):
                    raise ForbiddenException("您没有权限分配空间管理人员。")
                # 如果是空间管理员更改 managed_by，必须是改成自己或从自己改成 None
                if user.is_space_manager and not is_system_admin:
                    if new_managed_by and new_managed_by != user:
                        raise ForbiddenException(detail="空间管理员只能将自己的空间分配给自己。")
                    if old_managed_by == user and new_managed_by != user and new_managed_by is not None:
                        raise ForbiddenException(detail="空间管理员只能将自己管理的空间的管理人改为自己或清空。")

            # 检查字段级权限 (使用 obj 形式的权限检查，因为 guardian 设计为检查对象)
            # 这里我将 `is_space_manager_group_member` 替换为 `user.is_space_manager` property
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

            # NEW: 检查 check_in_by 字段的权限
            if check_in_by_data is not None and set(u.pk for u in check_in_by_data) != set(
                    user.pk for user in space.check_in_by.all()):
                # 只有系统管理员或有 'can_assign_space_manager' 权限（这里复用此权限，或者定义新的如 can_manage_check_in_staff）才能修改
                if not (is_system_admin or user.has_perm('spaces.can_assign_space_manager', space)):
                    raise ForbiddenException("您没有权限修改空间的可签到人员。")
            # --- 权限校验 END ---

            updated_space = self._space_dao.update(space, **space_data)

            if permitted_groups_data is not None:
                updated_space.permitted_groups.set(permitted_groups_data)

            if amenity_ids_data is not None:
                self._update_space_amenities(updated_space, amenity_ids_data, user) # _update_space_amenities 内部有权限检查

            # NEW: 处理 check_in_by 字段
            if check_in_by_data is not None:
                updated_space.check_in_by.set(check_in_by_data) # 直接设置 ManyToMany 关系
                # updated_space.save() 方法中已包含对 check_in_by 权限的分配和组管理，无需在此处重复

            # 缓存失效将在信号处理中完成，避免在此处重复调用
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
                # 尝试以权限方式再获取一次，如果仍获取不到，则确实是未找到或无权限
                # _space_dao.get_space_by_id 已经包含了权限过滤，这里直接抛出 NotFoundException
                raise NotFoundException(detail="空间未找到。")

            # --- 权限校验 ---
            is_system_admin = user.is_system_admin

            # 对于非系统管理员的空间管理员，只能删除自己管理的空间
            if user.is_space_manager and not is_system_admin and space.managed_by != user:
                raise ForbiddenException(detail="您没有权限删除此空间。")

            if not (is_system_admin or user.has_perm('spaces.can_delete_space', space)):
                raise ForbiddenException(detail="您没有权限删除此空间。")
            # --- 权限校验 END ---

            # NEW: 在删除空间前，需要先清理所有关联的 check_in_by 权限
            # 否则，如果用户被删除但权限没有被清理，可能会导致一些不一致
            if space.check_in_by.exists():
                for staff_user in space.check_in_by.all():
                    remove_perm('can_check_in_real_space', staff_user, space)
                # 移除关联后，Django 会自动在 save() 或 set([]) 中处理 ManyToMany 字段，
                # 但直接删除对象时，我们主动移除权限可以确保一致性。

            if space.child_spaces.exists():
                raise BadRequestException(detail="存在子空间，无法删除此空间。请先删除或解除所有子空间与此空间的关联。")

            from bookings.models import Booking
            if Booking.objects.filter(Q(space=space) | Q(bookable_amenity__space=space)).exists():
                raise BadRequestException(detail="存在关联的预订记录，无法删除此空间。请先处理所有预订记录。")

            self._space_dao.delete(space)

            # 缓存失效将在信号处理中完成，避免在此处重复调用
            return ServiceResult.success_result(
                message="空间删除成功。",
                status_code=204
            )
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"删除空间失败 (ID: {pk}, 用户: {user.username})。")
            return self._handle_exception(e, default_message="删除空间失败。", default_status_code=500)

    @transaction.atomic
    def _update_space_amenities(self, space: Space, amenity_ids: Optional[List[int]], user: CustomUser):
        if amenity_ids is None:
            return

        existing_ba_map = {
            ba.amenity_id: ba for ba in
            self._bookableamenity_dao.get_bookable_amenities_for_space_by_owner(space.pk, user)
        }

        new_amenity_ids_set = set(amenity_ids)
        current_amenity_ids_set = set(existing_ba_map.keys())

        # --- 权限校验 ---
        is_system_admin = user.is_system_admin
        is_space_manager_of_this_space = user.is_space_manager and space.managed_by == user

        if not (is_system_admin or is_space_manager_of_this_space):
            raise ForbiddenException(detail=f"您没有权限管理空间 '{space.name}' 的设施。")
        # --- 权限校验 END ---

        amenities_to_add = new_amenity_ids_set - current_amenity_ids_set
        for amenity_id in amenities_to_add:
            amenity_obj = self._amenity_dao.get_by_id(amenity_id)
            if not amenity_obj:
                raise BadRequestException(f"设施类型ID {amenity_id} 未找到。")

            # 可以在这里添加更细粒度的“添加设施”权限，但目前由上面的总管理权限覆盖
            # if not (is_system_admin or user.has_perm('spaces.can_add_space_amenity', space)):
            #     raise ForbiddenException(f"您没有权限向空间 '{space.name}' 添加设施类型 '{amenity_obj.name}'。")

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

            # 可以在这里添加更细粒度的“删除设施”权限，但目前由上面的总管理权限覆盖
            # if not (is_system_admin or user.has_perm('spaces.can_delete_bookable_amenity', ba_to_remove)):
            #     raise ForbiddenException(
            #         f"您没有权限从空间 '{space.name}' 移除设施实例 '{ba_to_remove.amenity.name}' (PK: {ba_to_remove.id})。")

            from bookings.models import Booking as BookingModelForCheck
            if BookingModelForCheck.objects.filter(bookable_amenity=ba_to_remove).exists():
                raise BadRequestException(f"设施实例 '{ba_to_remove.amenity.name}' 存在关联的预订记录，无法移除。")

            self._bookableamenity_dao.delete(ba_to_remove)
            logger.debug(
                f"Removed bookable amenity {ba_to_remove.amenity.name} (PK:{ba_to_remove.pk}) from space {space.id}.")

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