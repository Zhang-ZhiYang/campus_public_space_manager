# spaces/dao/space_dao.py
from typing import Optional, List
from django.db.models import QuerySet
from core.dao import BaseDAO
from spaces.models import Space, BookableAmenity  # , CustomUser # CustomUser 在 DAO 中通常不需要直接导入
from guardian.shortcuts import get_objects_for_user

class SpaceDAO(BaseDAO):
    model = Space

    def get_queryset(self) -> QuerySet[Space]:
        """
        获取基础 Space QuerySet，并预加载常用关联对象以优化查询。
        FIX: 将 'restricted_groups' 替换为 'permitted_groups'。
        """
        return super().get_queryset().select_related(
            'space_type',
            'parent_space',
            'managed_by'
        ).prefetch_related(
            # 'children_spaces',  # 预加载子空间
            # FIX: 原来的 'restricted_groups' 必须改为 'permitted_groups'
            'permitted_groups'  # 这是关键的修正！
        )

    def get_by_id(self, pk: int) -> Optional[Space]:
        """按ID获取单个空间，并确保使用预加载的 queryset。"""
        try:
            return self.get_queryset().get(pk=pk)
        except self.model.DoesNotExist:
            return None

    def get_spaces_for_user_management(self, user) -> QuerySet[Space]:
        """
        获取用户有权限管理的空间。
        这通常用于空间管理员，他们可以管理那些由他们或他们的团队管理的特定空间。
        """
        # 移除了所有 is_xxx 权限检查，现在由 Service 层/视图层装饰器处理
        # 始终通过 guardian 过滤，因为这是对象级权限的列表视图
        return get_objects_for_user(user, 'spaces.can_manage_space_details', klass=self.get_queryset()).filter(is_active=True, is_bookable=True)

    def space_has_children(self, space: Space) -> bool:
        """检查空间是否有子空间。"""
        # 使用 related_name 'children_spaces'
        return space.child_spaces.exists()

    def space_has_bookings(self, space: Space, BookingModel) -> bool:
        """
        检查空间是否有活跃或待处理的预订记录。
        需要传入 BookingModel 以避免循环导入。
        """
        return BookingModel.objects.filter(
            space=space,
            status__in=['PENDING', 'APPROVED', 'CHECKED_IN']  # 仅考虑活跃或待处理的预订
        ).exists() or BookingModel.objects.filter(
            bookable_amenity__space=space,
            status__in=['PENDING', 'APPROVED', 'CHECKED_IN']
        ).exists()

class BookableAmenityDAO(BaseDAO):
    model = BookableAmenity

    def get_queryset(self) -> QuerySet[BookableAmenity]:
        """
        获取基础 BookableAmenity QuerySet，并预加载常用关联对象以优化查询。
        """
        return super().get_queryset().select_related(
            'space__space_type',
            'amenity'
        )

    def get_bookable_amenity_by_id(self, pk: int) -> Optional[BookableAmenity]:
        """根据ID获取单个可预订设施实例。"""
        try:
            return self.get_queryset().get(pk=pk)
        except self.model.DoesNotExist:
            return None

    def get_bookable_amenities_for_space(self, space: Space) -> QuerySet[BookableAmenity]:
        """获取指定空间下的所有可预订设施实例。"""
        return self.get_queryset().filter(space=space)