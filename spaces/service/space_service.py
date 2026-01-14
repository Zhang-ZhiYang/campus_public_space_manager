# spaces/service/space_service.py (终极修订版，Service 列表方法不再缓存，创建更新逻辑精简)
import logging
from typing import List, Dict, Any, Union, Optional
from django.db.models import QuerySet, Q
from django.db import transaction

from core.service import BaseService, ServiceResult
from core.dao import DAOFactory
from core.cache import CacheService  # Use the now updated CacheService
from core.utils.exceptions import ServiceException, NotFoundException, BadRequestException, \
    ForbiddenException, CustomAPIException
from spaces.models import Space, SpaceType, Amenity, BookableAmenity
from users.models import CustomUser
from django.contrib.auth.models import Group

# from bookings.models import Booking # Deferred import in methods to avoid circular dependency if Booking uses SpaceService

logger = logging.getLogger(__name__)


class SpaceService(BaseService):
    """
    负责处理 Space 模型相关的业务逻辑。
    """
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

    # NO @CacheService.cache_method here. List caching now happens in the View.
    def get_all_spaces(self, user: CustomUser) -> ServiceResult[QuerySet[Space]]:
        """
        获取所有 Space 实例的 QuerySet，支持基于权限的过滤和预加载。
        列表缓存逻辑已移动到 View 层，以兼容 DRF 的 filter_backends 和 Pagination。
        """
        logger.debug(f"Service: 获取所有空间 QuerySet (User: {user.username}).")

        db_queryset = self._space_dao.get_all_spaces(
            user=user,
            prefetch_related=self._allowed_prefetch_related,
            select_related=self._allowed_select_related
        )

        return ServiceResult.success_result(data=db_queryset)

    @CacheService.cache_method(key_prefix='spaces:space:detail', identifier_arg='pk')  # Explicit identifier_arg
    def get_space_by_id(self, user: CustomUser, pk: int) -> ServiceResult[Dict[str, Any]]:
        """
        获取单个 Space 实例，支持权限检查。
        此方法将从DAO获取Model实例，转换为Dict后进行缓存。
        """
        logger.debug(f"空间详情未命中缓存，从数据库加载并存入缓存 (PK:{pk}, User: {user.username}).")

        space = self._space_dao.get_space_by_id(
            user=user,
            pk=pk,
            prefetch_related=self._allowed_prefetch_related,
            select_related=self._allowed_select_related
        )

        if not space:
            raise NotFoundException(detail=f"Space with ID {pk} not found or you do not have permission to view it.")

        space_dict = space.to_dict(include_related=True)
        # The CacheService.cache_method decorator actually handles setting the cache after this return.
        # logger.info(f"Set key 'spaces:space:detail:{pk}' with timeout {CacheService.get_timeout_for_key_prefix('spaces:space:detail')}s (handled by decorator).")

        return ServiceResult.success_result(data=space_dict)

    @transaction.atomic
    def create_space(self, user: CustomUser,
                     space_data: Dict[str, Any],
                     # validated_data from serializer, now contains FK instances like {'space_type': <SpaceType object>}
                     permitted_groups_data: Optional[List[Group]] = None,
                     amenity_ids_data: Optional[List[int]] = None
                     ) -> ServiceResult[Dict[str, Any]]:  # Returns dict for consistency with get_by_id cache
        """
        创建新的空间。
        Service 层负责处理所有业务逻辑，包括设置FK实例、M2M关系和O2M关系。
        `space_data` 从序列化器接收，其中包含 ForeignKey 的实例 (e.g., `space_data['space_type']` 是 `SpaceType` 实例)。
        """
        try:
            # Create the main Space instance via DAO. Direct FK instances in `space_data` are handled by Django ORM.
            new_space = self._space_dao.create(**space_data)

            # Handle M2M relationships (permitted_groups)
            if permitted_groups_data is not None:  # Use `is not None` to allow empty lists
                new_space.permitted_groups.set(permitted_groups_data)

            # Handle O2M relationships (BookableAmenity)
            if amenity_ids_data is not None:
                self._update_space_amenities(new_space, amenity_ids_data, user)

            return ServiceResult.success_result(
                data=new_space.to_dict(),  # Return dict for consistency
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
                     space_data: Dict[str, Any],  # validated_data from serializer, contains FK instances
                     permitted_groups_data: Optional[List[Group]] = None,
                     amenity_ids_data: Optional[List[int]] = None
                     ) -> ServiceResult[Dict[str, Any]]:  # Returns dict for consistency with get_by_id cache
        """
        更新空间。
        Service 层进行细粒度对象级权限检查，并处理 M2M 和 O2M 关系。
        `space_data` 从序列化器接收，其中包含 ForeignKey 的实例。
        """
        try:
            space = self._space_dao.get_space_by_id(user=user, pk=pk)
            if not space:
                raise NotFoundException(detail="空间未找到或您没有权限。")

            # --- 权限检查 for update actions ---
            is_system_admin = getattr(user, 'is_system_admin', False) or user.is_superuser
            is_space_manager_group_member = user.is_staff and user.groups.filter(name='空间管理员').exists()

            # Check for changes in `space_type`
            if 'space_type' in space_data and space_data['space_type'] != space.space_type:
                if not is_system_admin:
                    raise ForbiddenException(f"您没有权限更改空间类型。")

            # Check for changes in `managed_by`
            new_manager_target = space_data.get('managed_by')  # This is the full CustomUser instance or None
            new_manager_pk = new_manager_target.pk if new_manager_target else None
            old_manager_pk = space.managed_by.pk if space.managed_by else None

            if 'managed_by' in space_data and new_manager_pk != old_manager_pk:
                if not (is_system_admin or user.has_perm('spaces.can_assign_space_manager', space)):
                    raise ForbiddenException(f"您没有权限分配空间管理人员。")

            # General edit space info fields
            editable_info_fields = ['name', 'location', 'description', 'capacity', 'image']
            if any(f in space_data for f in editable_info_fields) and not (
                    is_system_admin or is_space_manager_group_member or user.has_perm('spaces.can_edit_space_info',
                                                                                      space)):
                raise ForbiddenException(f"您没有权限编辑空间基本信息。")

            # Status related fields
            status_fields = ['is_active', 'is_bookable', 'is_container']
            if any(f in space_data for f in status_fields) and not (
                    is_system_admin or is_space_manager_group_member or user.has_perm('spaces.can_change_space_status',
                                                                                      space)):
                raise ForbiddenException(f"您没有权限更改空间状态。")

            # Booking rules related fields
            booking_rule_fields = ['requires_approval', 'available_start_time', 'available_end_time',
                                   'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes']
            if any(f in space_data for f in booking_rule_fields) and not (
                    is_system_admin or is_space_manager_group_member or user.has_perm(
                'spaces.can_configure_booking_rules', space)):
                raise ForbiddenException(f"您没有权限配置预订规则。")

            # Update the main Space instance via DAO
            updated_space = self._space_dao.update(space, **space_data)

            # Handle M2M relationships (permitted_groups)
            if permitted_groups_data is not None:
                updated_space.permitted_groups.set(permitted_groups_data)

            # Handle O2M relationships (BookableAmenity)
            if amenity_ids_data is not None:
                self._update_space_amenities(updated_space, amenity_ids_data, user)

            return ServiceResult.success_result(
                data=updated_space.to_dict(),  # Return dict for consistency
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
        删除空间。
        """
        try:
            space = self._space_dao.get_space_by_id(user=user, pk=pk)
            if not space:
                raise NotFoundException(detail="空间未找到或您没有权限。")

            is_system_admin = getattr(user, 'is_system_admin', False) or user.is_superuser
            if not (is_system_admin or user.has_perm('spaces.can_delete_space', space)):
                raise ForbiddenException(detail="您没有权限删除此空间。")

            if space.child_spaces.exists():
                # Fix: Remove 'errors' argument
                raise BadRequestException(detail="存在子空间，无法删除此空间。请先删除或解除所有子空间与此空间的关联。")

            from bookings.models import Booking
            if Booking.objects.filter(Q(space=space) | Q(bookable_amenity__space=space)).exists():
                # Fix: Remove 'errors' argument
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
        """
        内部辅助方法：为空间更新其关联的 BookableAmenity 实例。
        在对每个具体 BookableAmenity 进行操作时，执行细粒度权限检查。
        """
        # Note: If amenity_ids is None, it means the client did not send this field,
        # implying no change is requested for amenities. We only process if explicitly sent.
        if amenity_ids is None:
            return

        existing_ba_map = {
            ba.amenity_id: ba for ba in self._bookableamenity_dao.get_bookable_amenities_for_space(space.pk, user)
        }

        new_amenity_ids_set = set(amenity_ids)
        current_amenity_ids_set = set(existing_ba_map.keys())

        is_system_admin = getattr(user, 'is_system_admin', False) or user.is_superuser

        # Determine amenities to add
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

        # Determine amenities to remove
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

    def _get_cache_postfix_for_user(self, user: CustomUser) -> str:
        """
        根据用户类型生成缓存后缀。
        """
        if user.is_superuser or getattr(user, 'is_system_admin', False):
            return 'admin'
        elif not user.is_authenticated:
            return 'anonymous'
        elif user.is_staff and user.groups.filter(name='空间管理员').exists():
            return 'admin_manager'
        else:
            return f'user_{user.pk}'