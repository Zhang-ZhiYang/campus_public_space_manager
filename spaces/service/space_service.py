# spaces/service/space_service.py (修订版)
import logging
from typing import List, Dict, Any, Optional

from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import QuerySet, Q
from core.service import BaseService, ServiceResult
# 仅导入 CustomAPIException 基类和业务异常，移除了 Service 层不直接依赖的 DRF 异常
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, ConflictException, \
    CustomAPIException
from spaces.models import Space, Amenity, BookableAmenity  # , SpaceType # SpaceType 被直接引用，但未直接导入
from django.contrib.auth import get_user_model
from guardian.shortcuts import assign_perm, remove_perm, get_objects_for_user
from bookings.models import Booking  # 导入 Booking 模型，用于检查关联预订

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
        获取所有空间列表。此方法负责根据用户角色和对象属性过滤可访问空间。
        """
        try:
            base_qs = self.space_dao.get_queryset()

            if user.is_superuser or user.is_system_admin:
                spaces_qs = base_qs
            elif user.is_authenticated and user.is_space_manager:
                # 空间管理员可以查看他们有 'spaces.can_view_space' 权限管理的所有空间
                # 注意：这里返回的 queryset 应该包含所有状态的空间，以便管理员看到完整列表
                spaces_qs = get_objects_for_user(user, 'spaces.can_view_space', klass=base_qs)
            elif user.is_authenticated:
                # 普通用户 (非管理员) 只能看到 'active' 且 'bookable' 的空间
                base_qs = base_qs.filter(is_active=True, is_bookable=True)

                basic_infra_condition = Q(space_type__is_basic_infrastructure=True)
                user_groups_pks = list(user.groups.values_list('pk', flat=True))
                group_permitted_condition = Q(pk__in=[])

                if user_groups_pks:
                    # Permitted_groups 规则仅适用于非基础型基础设施
                    group_permitted_condition = Q(
                        space_type__is_basic_infrastructure=False,
                        permitted_groups__in=user_groups_pks
                    )
                # 普通用户也可以查看被明确授予 'can_view_space' 权限的空间
                explicitly_viewable_spaces = get_objects_for_user(user, 'spaces.can_view_space', klass=base_qs)

                spaces_qs = base_qs.filter(
                    basic_infra_condition | group_permitted_condition | Q(
                        pk__in=explicitly_viewable_spaces.values_list('pk', flat=True))
                ).distinct()
            else:
                # 未认证用户只能看到 active, bookable, 且为 basic_infrastructure 的空间
                spaces_qs = base_qs.filter(is_active=True, is_bookable=True, space_type__is_basic_infrastructure=True)

            return ServiceResult.success_result(
                data=spaces_qs,
                message="成功获取空间列表。",
                status_code=200
            )
        except Exception as e:
            logger.exception(f"获取所有空间列表失败 (用户: {user.username})。")
            return self._handle_exception(e, default_message="获取空间列表失败。")

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

            # --- 权限检查逻辑 ---
            can_view = False
            if user.is_superuser or user.is_system_admin:
                can_view = True
            elif user.is_authenticated:
                # 首先检查直接的对象级权限
                if user.has_perm('spaces.can_view_space', space):
                    can_view = True
                else:
                    # 检查是否为基础型设施 或者 用户属于允许的分组
                    is_basic_infrastructure = space.space_type and space.space_type.is_basic_infrastructure
                    user_is_in_permitted_groups = False
                    if space.permitted_groups.exists() and user.groups.exists():
                        user_is_in_permitted_groups = user.groups.filter(pk__in=space.permitted_groups.all()).exists()

                    if is_basic_infrastructure or user_is_in_permitted_groups:
                        can_view = True

                    # 对于非管理员用户，进一步限制只能看到活跃且可预订的空间
                    if not user.is_staff_member and (not space.is_active or not space.is_bookable):
                        can_view = False
            else:  # 未认证用户只能看 is_basic_infrastructure 且 active, bookable 的空间
                if space.space_type and space.space_type.is_basic_infrastructure and space.is_active and space.is_bookable:
                    can_view = True

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
            logger.exception(f"获取空间详情失败 (ID: {pk}, 用户: {user.username})。")
            return self._handle_exception(e, default_message="获取空间详情失败。")

    @transaction.atomic
    def create_space(self, user: CustomUser,
                     space_data: Dict[str, Any],
                     permitted_groups_data: Optional[List[Group]] = None,  # 明确接收 Group 实例列表
                     amenity_ids_data: Optional[List[int]] = None  # 明确接收整数 ID 列表
                     ) -> ServiceResult[Space]:
        """
        创建新的空间。权限已在视图层通过装饰器检查 (@is_admin_or_space_manager_required)。
        Service 层负责业务逻辑以及在创建**后**的 initial `guardian` 对象级权限分配。
        """
        if amenity_ids_data is None:
            amenity_ids_data = []
        if permitted_groups_data is None:
            permitted_groups_data = []

        # 确保这些 M2M 字段不在 space_data 中，因为它们将通过 .set() 方式处理
        # 即使它们被错误地传入 space_data，也在此处移除
        space_data.pop('permitted_groups', None)
        space_data.pop('amenity_ids', None)  # 确保移除，虽然通常不会从视图层作为 amenity_ids 传入 space_data

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
            else:
                space_data['managed_by'] = None

            new_space = self.space_dao.create(**space_data)

            # 在空间创建后，使用 .set() 方法处理多对多关系
            if permitted_groups_data:  # 检查列表是否为空
                new_space.permitted_groups.set(permitted_groups_data)

                # 处理可预订设施实例
            if amenity_ids_data:
                self._update_space_amenities(new_space, amenity_ids_data, user)

            return ServiceResult.success_result(
                data=new_space,
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
                     permitted_groups_data: Optional[List[Group]] = None,  # 明确接收 Group 实例列表
                     amenity_ids_data: Optional[List[int]] = None  # 明确接收整数 ID 列表
                     ) -> ServiceResult[Space]:
        """
        更新空间。视图层确保用户已认证 (@is_admin_or_space_manager_required)。
        Service 层在这里进行细粒度对象级权限检查。
        """
        # amenity_ids_data 和 permitted_groups_data 可能是 None (如果 PATCH 请求中未提供)
        # 或空列表 (如果提供了但为空)，或包含数据的列表

        # 确保移除 space_data 中可能存在的 permitted_groups 和 amenity_ids
        space_data.pop('permitted_groups', None)
        space_data.pop('amenity_ids', None)

        managed_by_id = space_data.pop('managed_by_id', False)  # `False` 表示此字段没有在 space_data 中

        try:
            space = self.space_dao.get_by_id(pk)
            if not space:
                return ServiceResult.error_result(
                    message="空间未找到。",
                    error_code=NotFoundException.default_code,
                    status_code=NotFoundException.status_code
                )

            # --- 权限检查 (这里我们只检查是否尝试修改了这些字段) ---
            if not user.is_system_admin:
                if 'managed_by_id' in space_data or managed_by_id is not False:  # 如果 request.data 中包含 managed_by_id
                    if not user.has_perm('spaces.can_assign_space_manager', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无分配空间管理人员权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )
                if permitted_groups_data is not None:  # 如果 request.data 中包含 permitted_groups
                    if not user.has_perm('spaces.can_manage_permitted_groups', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无管理可预订用户组权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )

                # ... (其他字段的权限检查保持不变) ...
                edited_fields = set(space_data.keys())
                if any(f in edited_fields for f in ['name', 'location', 'description', 'capacity', 'image']):
                    if not user.has_perm('spaces.can_edit_space_info', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无编辑空间基本信息权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )

                if any(f in edited_fields for f in ['is_active', 'is_bookable', 'is_container', 'space_type_id']):
                    if not user.has_perm('spaces.can_change_space_status', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无更改空间状态或类型权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )

                if any(f in edited_fields for f in ['requires_approval', 'available_start_time', 'available_end_time',
                                                    'min_booking_duration', 'max_booking_duration',
                                                    'buffer_time_minutes']):
                    if not user.has_perm('spaces.can_configure_booking_rules', space):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无配置预订规则权限)",
                            error_code=ForbiddenException.default_code, status_code=ForbiddenException.status_code
                        )

                if amenity_ids_data is not None:
                    if not (user.is_system_admin or
                            user.has_perm('spaces.can_add_space_amenity', space) or
                            user.has_perm('spaces.can_remove_space_amenity', space)):
                        return ServiceResult.error_result(
                            message=ForbiddenException.default_detail + " (无管理空间设施列表权限)",
                            error_code=ForbiddenException.default_code,
                            status_code=ForbiddenException.status_code
                        )

            # 处理 managed_by
            if managed_by_id is not False:
                if managed_by_id is None:
                    space_data['managed_by'] = None
                else:
                    try:
                        managed_by_user_instance = CustomUser.objects.get(pk=managed_by_id)
                        space_data['managed_by'] = managed_by_user_instance
                    except CustomUser.DoesNotExist:
                        return ServiceResult.error_result(
                            message=f"管理人员ID {managed_by_id} 不存在。",
                            error_code=BadRequestException.default_code,
                            status_code=BadRequestException.status_code
                        )

            updated_space = self.space_dao.update(space, **space_data)

            # 在空间更新后，使用 .set() 方法处理多对多关系
            if permitted_groups_data is not None:  # 仅当请求中明确提供了此字段时才更新
                updated_space.permitted_groups.set(permitted_groups_data)

            # 更新设施
            if amenity_ids_data is not None:  # 仅当请求中明确提供了此字段时才更新
                self._update_space_amenities(updated_space, amenity_ids_data, user)

            return ServiceResult.success_result(
                data=updated_space,
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
        """
        删除空间。视图层确保用户已认证 (@is_admin_or_space_manager_required)。
        Service 层在这里进行对象级权限检查（'spaces.can_delete_space'）。
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

            if space.child_spaces.exists():  # 使用 related_name
                return ServiceResult.error_result(
                    message="存在子空间，无法删除此空间。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code,
                    errors=["请先删除或解除所有子空间与此空间的关联。"]
                )

            # 使用 Booking Model 检查是否有关联的预订
            # 导入 Booking Model 在函数内部，避免循环依赖
            from bookings.models import Booking
            if Booking.objects.filter(Q(space=space) | Q(bookable_amenity__space=space)).exists():
                return ServiceResult.error_result(
                    message="存在关联的预订记录，无法删除此空间。",
                    error_code=BadRequestException.default_code,
                    status_code=BadRequestException.status_code,
                    errors=["请先处理所有预订记录。"]
                )

            self.space_dao.delete(space)
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
    def _update_space_amenities(self, space: Space, amenity_ids: List[int], user: CustomUser):
        """
        内部辅助方法：为空间更新其关联的 BookableAmenity 实例。
        在对每个具体 BookableAmenity 进行操作时，执行细粒度权限检查。
        """
        existing_amenity_map = {
            ba.amenity_id: ba for ba in self.bookable_amenity_dao.get_bookable_amenities_for_space(space)
        }

        # 新增或更新 BookableAmenity
        for amenity_id in amenity_ids:
            amenity_obj = self.amenity_dao.get_by_id(amenity_id)
            if not amenity_obj:
                raise BadRequestException(f"设施类型ID {amenity_id} 未找到。")

            # 检查用户是否有权限向此空间添加此设施类型
            if not (user.is_system_admin or user.has_perm('spaces.can_add_space_amenity', space)):
                raise ForbiddenException(f"您没有权限向空间 '{space.name}' 添加设施类型 '{amenity_obj.name}'。")

            if amenity_id not in existing_amenity_map:
                # 假设默认数量为1，is_bookable 取决于 amenities.is_bookable_individually
                self.bookable_amenity_dao.create(
                    space=space,
                    amenity=amenity_obj,
                    quantity=1,
                    is_bookable=amenity_obj.is_bookable_individually,
                    is_active=True
                )
                logger.debug(f"Added new bookable amenity {amenity_obj.name} to space {space.id}.")
            # 如果存在，可以考虑在这里更新 quantity, is_active, is_bookable, 但当前需求只涉及添加/移除
            # 如果需要更新，则需检查 can_edit_bookable_amenity_quantity 等权限

        # 移除不再需要的 BookableAmenity
        for ba in existing_amenity_map.values():
            if ba.amenity_id not in amenity_ids:
                # 检查用户是否有权限从空间移除此设施实例
                if not (user.is_system_admin or user.has_perm('spaces.can_delete_bookable_amenity',
                                                              ba) or user.has_perm('spaces.can_remove_space_amenity',
                                                                                   space)):
                    raise ForbiddenException(f"您没有权限从空间 '{space.name}' 移除设施实例 '{ba.amenity.name}'。")

                # 在删除BookableAmenity之前，检查是否有其自身的预订记录
                if ba.amenity_bookings.exists():  # 假设 BookableAmenity 有 related_name 'amenity_bookings'
                    raise BadRequestException(f"设施实例 '{ba.amenity.name}' 存在关联的预订记录，无法移除。")

                self.bookable_amenity_dao.delete(ba)
                logger.debug(f"Removed bookable amenity {ba.amenity.name} from space {space.id}.")