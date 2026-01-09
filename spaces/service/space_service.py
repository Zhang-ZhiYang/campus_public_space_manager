# spaces/service/space_service.py (修订版)
import logging
from typing import List, Dict, Any
from django.db import transaction
from django.db.models import QuerySet, Q

from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, ConflictException
from spaces.models import Space, Amenity, BookableAmenity, SpaceType
from django.contrib.auth import get_user_model
from guardian.shortcuts import get_objects_for_user, assign_perm, remove_perm  # 确保导入 remove_perm

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


class SpaceService(BaseService):
    _dao_map = {
        'space_dao': 'space',
        'amenity_dao': 'amenity',
        'bookable_amenity_dao': 'bookable_amenity',
    }

    # get_all_spaces: 保持现状，其内的 is_superuser/is_system_admin/is_space_manager 等判断是数据过滤逻辑
    def get_all_spaces(self, user: CustomUser) -> ServiceResult[QuerySet[Space]]:
        """
        获取所有空间列表。此方法负责根据用户角色和对象属性过滤可访问空间。
        """
        try:
            base_qs = self.space_dao.get_queryset().filter(is_active=True, is_bookable=True)

            if user.is_superuser or user.is_system_admin:
                spaces_qs = base_qs
            elif user.is_authenticated and user.is_space_manager:
                # 空间管理员可以查看他们有 'can_view_space' 权限管理的所有活跃且可预订的空间
                spaces_qs = get_objects_for_user(user, 'spaces.can_view_space', klass=base_qs)
            elif user.is_authenticated:
                basic_infra_condition = Q(space_type__is_basic_infrastructure=True)
                user_groups_pks = list(user.groups.values_list('pk', flat=True))
                group_permitted_condition = Q(pk__in=[])
                if user_groups_pks:
                    group_permitted_condition = Q(
                        space_type__is_basic_infrastructure=False,
                        permitted_groups__in=user_groups_pks
                    )
                # 普通用户也可以查看被明确授予 'can_view_space' 权限的空间
                explicitly_viewable_spaces = get_objects_for_user(user, 'spaces.can_view_space', klass=base_qs)
                spaces_qs = base_qs.filter(
                    basic_infra_condition | group_permitted_condition | Q(pk__in=explicitly_viewable_spaces)
                ).distinct()
            else:
                spaces_qs = base_qs.none()  # 未认证用户

            return ServiceResult.success_result(
                data=spaces_qs,
                message="成功获取空间列表。",
                status_code=200
            )
        except Exception as e:
            logger.exception(f"Exception getting all spaces for user {user.username}.")
            return self._handle_exception(e, default_message="获取空间列表失败。")

    # get_space_by_id: 权限逻辑更新
    def get_space_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Space]:
        """
        根据ID获取单个空间详情。整合对象级权限和数据可见性规则。
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
            # 系统管理员和超级管理员可以直接查看
            if user.is_superuser or user.is_system_admin:
                can_view = True
            # 其他已认证用户需通过更细致规则
            elif user.is_authenticated:
                # 1. 直接检查对象级 'can_view_space' 权限
                if user.has_perm('spaces.can_view_space', space):
                    can_view = True
                else:
                    # 2. 检查基本设施和用户组白名单可见性规则
                    is_basic_infrastructure = space.space_type and space.space_type.is_basic_infrastructure
                    user_is_in_permitted_groups = False
                    if space.permitted_groups.exists() and user.groups.exists():
                        user_is_in_permitted_groups = user.groups.filter(pk__in=space.permitted_groups.all()).exists()

                    if is_basic_infrastructure or (not is_basic_infrastructure and user_is_in_permitted_groups):
                        can_view = True

            # 额外的业务状态检查: 只有 Staff 才能查看不活跃/不可预订的空间
            if can_view and (not space.is_active or not space.is_bookable) and not user.is_staff_member:
                can_view = False

            if not can_view:
                return ServiceResult.error_result(
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
            logger.exception(f"Exception in get_space_by_id for user {user.username}, space {pk}.")
            return self._handle_exception(e, default_message="获取空间详情失败。")

    # create_space: 保持不变，View 层装饰器负责角色，signal 负责初始对象级权限分配
    @transaction.atomic
    def create_space(self, user: CustomUser, space_data: Dict[str, Any]) -> ServiceResult[Space]:
        """
        创建新的空间。
        权限已在视图层通过装饰器检查 (@is_admin_or_space_manager_required)。
        Service 层负责业务逻辑和 initial `guardian` 对象级权限分配。
        """
        amenity_ids = space_data.pop('amenity_ids', [])
        managed_by_id = space_data.pop('managed_by_id', None)

        try:
            if managed_by_id:
                try:
                    managed_by_user = CustomUser.objects.get(pk=managed_by_id)
                    space_data['managed_by'] = managed_by_user
                except CustomUser.DoesNotExist:
                    return ServiceResult.error_result(
                        message=f"管理人员ID {managed_by_id} 不存在。",
                        error_code=BadRequestException.default_code,
                        status_code=BadRequestException.status_code
                    )

            new_space = self.space_dao.create(**space_data)

            # 这部分逻辑由 post_save 信号处理，简化 Service 层
            # if user.is_space_manager and not user.is_system_admin:
            #     if not new_space.managed_by:
            #         new_space.managed_by = user
            #         new_space.save(update_fields=['managed_by']) # Force save before signal
            #     # Permissions are now assigned by post_save signal
            #     # assign_perm('spaces.can_manage_space_details', user, new_space)
            #     # assign_perm('spaces.can_manage_space_amenities', user, new_space)
            #     # assign_perm('spaces.can_manage_space_bookings', user, new_space)

            # _update_space_amenities 会在 Space 保存后，由 post_save 信号中的逻辑调用
            # 或者手动调用，但是权限检查应该在这里进行
            if amenity_ids:  # 如果有设施ID传入，更新关联设施，并检查权限
                # 假设创建空间的用户有添加设施的权限 (通过 View 层装饰器 + Service 层在此的细粒度检查)
                if not (user.is_system_admin or  # 系统管理员
                        user.has_perm('spaces.can_edit_space_info', new_space) or  # 空间信息管理权限涵盖
                        user.has_perm('spaces.can_add_space_amenity', new_space)):  # 更具体的添加设施权限
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (无添加空间设施权限)",
                        error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                    )
                self._update_space_amenities(new_space, amenity_ids, user)  # 传入 user

            return ServiceResult.success_result(
                data=new_space,
                message="空间创建成功。",
                status_code=201
            )
        except Exception as e:
            logger.exception(f"Exception creating space by user {user.username}.")
            return self._handle_exception(e, default_message="创建空间失败。")

    # update_space: 细粒度权限检查
    @transaction.atomic
    def update_space(self, user: CustomUser, pk: int, space_data: Dict[str, Any]) -> ServiceResult[Space]:
        """
        更新空间。
        视图层确保用户已认证。Service 层在这里进行细粒度对象级权限检查。
        """
        try:
            space = self.space_dao.get_by_id(pk)
            if not space:
                return ServiceResult.error_result(
                    message="空间未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # --- 绕过系统管理员的权限检查 ---
            if not user.is_system_admin:
                # 分别检查不同字段的更新权限
                # 基本信息（名称、位置、描述、容量、图片）
                if any(k in space_data for k in ['name', 'location', 'description', 'capacity', 'image']):
                    if not user.has_perm('spaces.can_edit_space_info', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无编辑空间基本信息权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                # 状态（is_active, is_bookable, is_container）
                if any(k in space_data for k in ['is_active', 'is_bookable', 'is_container']):
                    if not user.has_perm('spaces.can_change_space_status', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无更改空间状态权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                # 预订规则（requires_approval, available_start_time 等）
                if any(k in space_data for k in ['requires_approval', 'available_start_time', 'available_end_time',
                                                 'min_booking_duration', 'max_booking_duration',
                                                 'buffer_time_minutes']):
                    if not user.has_perm('spaces.can_configure_booking_rules', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无配置预订规则权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                # 分配管理人员（managed_by）
                if 'managed_by_id' in space_data:  # 检查是否尝试修改 managed_by 字段
                    if not user.has_perm('spaces.can_assign_space_manager', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无分配空间管理人员权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                # 管理可预订用户组 (permitted_groups)
                if 'permitted_groups' in space_data:
                    if not user.has_perm('spaces.can_manage_permitted_groups', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无管理可预订用户组权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                # 空间类型 (space_type_id) 的修改也可能是敏感操作
                if 'space_type_id' in space_data:  # 可视为 can_change_space_status 或更强的权限
                    if not user.has_perm('spaces.can_change_space_status', space):  # 或者自定义一个 can_change_space_type 权限
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无更改空间类型权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
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
                        return ServiceResult.error_result(
                            message=f"管理人员ID {managed_by_id} 不存在。",
                            error_code=BadRequestException.default_code,
                            status_code=BadRequestException.status_code
                        )

            updated_space = self.space_dao.update(space, **space_data)

            if amenity_ids is not None:
                # 在调用 _update_space_amenities 之前进行粗粒度的设施管理权限检查
                if not (user.is_system_admin or
                        user.has_perm('spaces.can_add_space_amenity', updated_space) or
                        user.has_perm('spaces.can_remove_space_amenity', updated_space)):
                    return ServiceResult.error_result(
                        message=ForbiddenException.default_detail + " (无管理空间设施列表权限)",
                        error_code=ForbiddenException.default_code,
                        status_code=ForbiddenException.status_code
                    )
                # _update_space_amenities 内部将对每个 BookableAmenity 进行细粒度权限检查
                self._update_space_amenities(updated_space, amenity_ids, user)

            return ServiceResult.success_result(
                data=updated_space,
                message="空间更新成功。",
                status_code=200
            )
        except Exception as e:
            logger.exception(f"Exception updating space {pk} by user {user.username}.")
            return self._handle_exception(e, default_message="更新空间失败。")

    # delete_space: 细粒度权限检查
    @transaction.atomic
    def delete_space(self, user: CustomUser, pk: int) -> ServiceResult[None]:
        """
        删除空间。
        视图层确保用户已认证。Service 层在这里进行对象级权限检查（'spaces.can_delete_space'）。
        """
        try:
            space = self.space_dao.get_by_id(pk)
            if not space:
                return ServiceResult.error_result(
                    message="空间未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # --- 细粒度删除权限检查 ---
            if not user.is_system_admin and not user.has_perm('spaces.can_delete_space', space):
                return ServiceResult.error_result(
                    message=ForbiddenException.default_detail + " (无删除空间权限)",
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
            logger.exception(f"Exception deleting space {pk} by user {user.username}.")
            return self._handle_exception(e, default_message="删除空间失败。")

    # _update_space_amenities: 内部辅助方法，增加每个 BookableAmenity 的细粒度权限检查
    @transaction.atomic
    def _update_space_amenities(self, space: Space, amenity_ids: List[int], user: CustomUser):
        """
        内部辅助方法：为空间更新其关联的 BookableAmenity 实例。
        在对每个具体 BookableAmenity 进行操作时，执行细粒度权限检查。
        """
        existing_amenity_map = {
            ba.amenity_id: ba for ba in self.bookable_amenity_dao.get_bookable_amenities_for_space(space)
        }

        amenities_to_add = []
        amenities_to_remove = []

        # Determine what to add and what to remove, and check permissions for each specific change
        for amenity_id in amenity_ids:
            if amenity_id not in existing_amenity_map:
                amenity_obj = self.amenity_dao.get_by_id(amenity_id)
                if not amenity_obj:
                    raise BadRequestException(f"设施类型ID {amenity_id} 未找到。")

                # 权限检查：能否向此空间添加此设施类型 (Space 对象上的 can_add_space_amenity)
                if not (user.is_system_admin or user.has_perm('spaces.can_add_space_amenity', space)):
                    raise ForbiddenException(f"您没有权限向空间 '{space.name}' 添加设施类型 '{amenity_obj.name}'。")

                amenities_to_add.append(
                    BookableAmenity(space=space, amenity=amenity_obj, quantity=1,
                                    is_bookable=amenity_obj.is_bookable_individually, is_active=True)  # 默认活跃
                )

        for ba in existing_amenity_map.values():
            if ba.amenity_id not in amenity_ids:
                # 权限检查：能否从此空间移除此设施实例 (BookableAmenity 对象上的 can_delete_bookable_amenity 或 Space 对象上的 can_remove_space_amenity)
                # 优先检查 BookableAmenity 的直接删除权限
                if not (user.is_system_admin or user.has_perm('spaces.can_delete_bookable_amenity', ba)
                        or (user.has_perm('spaces.can_remove_space_amenity', space))):  # Space 上的粗粒度移除权限
                    raise ForbiddenException(f"您没有权限从空间 '{space.name}' 移除设施实例 '{ba.amenity.name}'。")
                amenities_to_remove.append(ba)

        if amenities_to_add:
            new_bookable_amenities = self.bookable_amenity_dao.bulk_create(amenities_to_add)
            # 为新创建的 BookableAmenity 分配权限 (由 post_save 信号处理)
            logger.info(
                f"Added {len(new_bookable_amenities)} new bookable amenities to space {space.pk} by user {user.username}.")

        if amenities_to_remove:
            # 批量删除
            self.bookable_amenity_dao._manager.filter(pk__in=[ba.pk for ba in amenities_to_remove]).delete()
            logger.info(
                f"Removed {len(amenities_to_remove)} bookable amenities from space {space.pk} by user {user.username}.")