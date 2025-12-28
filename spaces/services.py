# spaces/services.py
from typing import List, Optional, Dict, Any
from django.db.models import QuerySet

from users.models import CustomUser
from spaces.models import Amenity, Space
from spaces.data_access import AmenityDataAccess, SpaceDataAccess
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException, ConflictException, \
    CustomAPIException
from core.utils.constants import MSG_FORBIDDEN


class AmenityService:
    """
    负责设施（Amenity）相关的业务逻辑。
    """

    @staticmethod
    def get_amenity(user: CustomUser, amenity_id: int) -> Amenity:
        """
        获取单个设施。所有认证用户都可以查看设施。
        """
        # 此时 request.user 已经是认证对象，IsAuthenticated 已经处理了未认证访问
        # 如果需要区分普通用户和管理员的可见性，可以在此添加逻辑
        return AmenityDataAccess.get_amenity_by_id(amenity_id)

    @staticmethod
    def list_amenities(user: CustomUser, filters: Optional[Dict[str, Any]] = None) -> QuerySet[Amenity]:
        """
        列出所有设施，可带过滤条件。所有认证用户都可以查看设施列表。
        """
        return AmenityDataAccess.list_amenities(**(filters or {}))

    @staticmethod
    def create_amenity(user: CustomUser, name: str, description: str) -> Amenity:
        """
        创建新设施。只有管理员和空间管理员有权限。
        """
        if not (user.is_admin or user.is_super_admin or user.is_space_manager):
            raise ForbiddenException(detail=MSG_FORBIDDEN)

        if not name or not name.strip():  # 业务逻辑验证：名称不能为空白
            raise BadRequestException(detail="设施名称不能为空。")

        return AmenityDataAccess.create_amenity(name=name, description=description)

    @staticmethod
    def update_amenity(user: CustomUser, amenity_id: int, amenity_data: Dict[str, Any]) -> Amenity:
        """
        更新设施信息。只有管理员和空间管理员有权限。
        """
        if not (user.is_admin or user.is_super_admin or user.is_space_manager):
            raise ForbiddenException(detail=MSG_FORBIDDEN)

        amenity = AmenityDataAccess.get_amenity_by_id(amenity_id)

        name = amenity_data.get('name')
        if name is not None and not name.strip():  # 业务逻辑验证：名称不能为空白
            raise BadRequestException(detail="设施名称不能为空。")

        return AmenityDataAccess.update_amenity(amenity, name=name, description=amenity_data.get('description'))

    @staticmethod
    def delete_amenity(user: CustomUser, amenity_id: int) -> None:
        """
        删除设施。只有管理员和空间管理员有权限。
        """
        if not (user.is_admin or user.is_super_admin or user.is_space_manager):
            raise ForbiddenException(detail=MSG_FORBIDDEN)

        amenity = AmenityDataAccess.get_amenity_by_id(amenity_id)
        AmenityDataAccess.delete_amenity(amenity)


class SpaceService:
    """
    负责空间（Space）相关的业务逻辑。
    """

    @staticmethod
    def get_space(user: CustomUser, space_id: int) -> Space:
        """
        获取单个空间。所有用户都可以查看。
        普通用户只能查看 is_active=True 和 is_bookable=True 的空间，管理员可以查看所有。
        """
        space = SpaceDataAccess.get_space_by_id(space_id)
        # 非管理员（即学生等普通用户）只能查看活跃且可预订的空间
        if not (user.is_admin or user.is_super_admin or user.is_space_manager) and \
                (not space.is_active or not space.is_bookable):
            # 伪装成未找到，避免泄露内部信息
            raise NotFoundException(detail=f"空间 {space_id} 未找到或不可用。")
        return space

    @staticmethod
    def list_spaces(user: CustomUser, filters: Optional[Dict[str, Any]] = None) -> QuerySet[Space]:
        """
        列出所有空间。普通用户只能看到 is_active=True 且 is_bookable=True 的空间。
        管理员/空间管理员可以看到所有 is_active=True 的空间。超级管理员可以看所有。
        """
        query_filters = filters or {}

        if user.is_super_admin:
            # 超级管理员可以看到所有空间，包括不活跃的
            pass
        elif user.is_admin or user.is_space_manager:
            # 系统管理员和空间管理员可以看到所有活跃空间（无论是否可预订）
            query_filters['is_active'] = True
        else:
            # 普通用户（如学生）只能看到活跃且可预订的空间
            query_filters['is_active'] = True
            query_filters['is_bookable'] = True

        return SpaceDataAccess.list_spaces(**query_filters)

    @staticmethod
    def create_space(user: CustomUser, space_data: Dict[str, Any], amenity_ids: Optional[List[int]]) -> Space:
        """
        创建新空间。只有管理员和空间管理员有权限。
        """
        if not (user.is_admin or user.is_super_admin or user.is_space_manager):
            raise ForbiddenException(detail=MSG_FORBIDDEN)

        # 业务逻辑验证：必需字段检查
        if not space_data.get('name') or not space_data.get('name').strip():
            raise BadRequestException(detail="空间名称不能为空。")
        if not space_data.get('location') or not space_data.get('location').strip():
            raise BadRequestException(detail="空间位置不能为空。")
        if space_data.get('capacity') is None or space_data.get('capacity') <= 0:
            raise BadRequestException(detail="空间容量必须大于0。")

        # 模型和序列化器中的验证已经处理了 available_start_time / available_end_time 关系

        return SpaceDataAccess.create_space(space_data, amenity_ids or [])

    @staticmethod
    def update_space(user: CustomUser, space_id: int, space_data: Dict[str, Any],
                     amenity_ids: Optional[List[int]] = None) -> Space:
        """
        更新空间信息及关联设施。只有管理员和空间管理员有权限。
        """
        if not (user.is_admin or user.is_super_admin or user.is_space_manager):
            raise ForbiddenException(detail=MSG_FORBIDDEN)

        space_instance = SpaceDataAccess.get_space_by_id(space_id)

        # 业务逻辑验证：如果提供了 name/location，则不能为空白
        if 'name' in space_data and not space_data['name'].strip():
            raise BadRequestException(detail="空间名称不能为空。")
        if 'location' in space_data and not space_data['location'].strip():
            raise BadRequestException(detail="空间位置不能为空。")
        if 'capacity' in space_data and space_data['capacity'] <= 0:
            raise BadRequestException(detail="空间容量必须大于0。")

        # 模型和序列化器中的验证已经处理了 available_start_time / available_end_time 关系

        return SpaceDataAccess.update_space(space_instance, space_data, amenity_ids)

    @staticmethod
    def delete_space(user: CustomUser, space_id: int) -> None:
        """
        删除空间。只有系统管理员和超级管理员有权限。(根据业务需求，空间管理员通常不具备删除空间的权限)
        """
        if not (user.is_admin or user.is_super_admin):
            raise ForbiddenException(detail=MSG_FORBIDDEN)

        space_instance = SpaceDataAccess.get_space_by_id(space_id)
        SpaceDataAccess.delete_space(space_instance)