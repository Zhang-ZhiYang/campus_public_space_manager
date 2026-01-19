# spaces/dao/space_dao.py - START OF FILE
from typing import Optional, List
from django.db.models import QuerySet, Q
from core.dao import BaseDAO
from spaces.models import Space, BookableAmenity
from users.models import CustomUser
from guardian.shortcuts import get_objects_for_user
import logging

logger = logging.getLogger(__name__)


class SpaceDAO(BaseDAO):
    """
    Space 数据的访问对象。
    提供了按用户权限获取所有空间、按ID获取单个空间以及其他辅助方法。
    """
    model = Space

    def get_base_queryset(self) -> QuerySet[Space]:
        """
        获取一个带有常用预加载的基础 Space QuerySet。
        该方法被其他 DAO 方法复用，以确保一致的预加载优化。
        """
        return self.model.objects.select_related(
            'space_type',
            'parent_space',
            'managed_by'
        ).prefetch_related(
            'permitted_groups',
            'child_spaces',
            'bookable_amenities__amenity'
        )

    def _apply_eager_loading(self, queryset: QuerySet[Space], prefetch_related: list = None,
                             select_related: list = None) -> QuerySet[Space]:
        """内部辅助方法，用于在基础QuerySet之上应用动态的预加载优化。"""
        if select_related:
            queryset = queryset.select_related(*select_related)
        if prefetch_related:
            queryset = queryset.prefetch_related(*prefetch_related)
        return queryset

    def get_all_spaces(self, user: CustomUser, prefetch_related: list = None, select_related: list = None) -> QuerySet[
        Space]:
        """
        获取所有 Space 对象的 QuerySet，并根据用户权限进行过滤。
        """
        queryset = self.get_base_queryset().order_by('name')

        if not user.is_superuser:  # 超级管理员可以查看所有，无需进一步过滤
            if user.is_authenticated:
                # 检查用户是否是系统管理员或空间管理员（通过 CustomUser 的属性判断）
                is_admin_or_manager = user.is_system_admin or user.is_space_manager

                if is_admin_or_manager:
                    # 对于系统管理员和空间管理员，只要是活跃的空间就可见 (或其管理的，或明确授权的)
                    # 这里不过滤 is_active，因为管理员可能需要查看不活跃的空间
                    pass
                else:  # 普通认证用户
                    explicitly_viewable_pks = get_objects_for_user(user, 'spaces.can_view_space',
                                                                   klass=Space).values_list('pk', flat=True)

                    # 联合查询：基础型基础设施 | 加入组权限 | 自己管理的 | 明确授予查看权限
                    queryset = queryset.filter(
                        Q(space_type__is_basic_infrastructure=True) |
                        Q(permitted_groups__in=user.groups.all()) |
                        Q(managed_by=user) |
                        Q(pk__in=explicitly_viewable_pks)
                    ).distinct()
            else:  # 匿名用户
                # 匿名用户只能查看活跃、可预订且是基础型基础设施的空间
                queryset = queryset.filter(
                    is_active=True,
                    is_bookable=True,
                    space_type__is_basic_infrastructure=True
                )

            # 对所有非系统管理员/超级管理员用户，默认只显示活跃的空间
            if not user.is_system_admin and not user.is_superuser:
                queryset = queryset.filter(is_active=True)

        return self._apply_eager_loading(queryset, prefetch_related, select_related)

    # --- 新增的方法：获取用户管理的或系统管理员可见的空间 ---
    def get_managed_spaces(self, user: CustomUser, prefetch_related: list = None, select_related: list = None) -> \
    QuerySet[Space]:
        """
        获取用户有权限管理（managed_by=user）的空间列表，或如果是系统管理员，则返回所有有 manager 的空间。
        """
        queryset = self.get_base_queryset().order_by('name')

        if user.is_system_admin:
            # 系统管理员可以看到所有有 managed_by 或 is_active 状态的空间
            # 这里不过滤 is_active，假设系统管理员需要看到所有状态的空间
            queryset = queryset  # 无需额外过滤，get_base_queryset 即可
        elif user.is_space_manager:
            # 空间管理员只能看自己管理的空间，且通常只看活跃的
            queryset = queryset.filter(managed_by=user)
            # 对于空间管理员，默认只显示活跃的空间
            queryset = queryset.filter(is_active=True)
        else:
            # 如果不是系统管理员也不是空间管理员，则不应有任何管理空间
            return self.model.objects.none()  # 返回空 QuerySet

        return self._apply_eager_loading(queryset, prefetch_related, select_related)

    # --- 新增的方法 END ---

    def get_space_by_id(self, user: CustomUser, pk: int, prefetch_related: list = None, select_related: list = None) -> \
            Optional[Space]:
        """
        根据 ID 获取单个 Space 对象，并根据用户权限进行过滤。
        返回单个 Space 对象或 None。
        """
        base_qs_filtered_by_pk = self.get_base_queryset().filter(pk=pk)

        if not user.is_superuser:
            is_admin_or_manager = user.is_system_admin or user.is_space_manager

            if is_admin_or_manager:
                pass  # 管理员角色可以查看所有（包括不活跃的），但仍然要检查是否是其管理的或者直接有权限的
            else:  # 普通认证用户或匿名用户
                explicitly_viewable_pks = get_objects_for_user(user, 'spaces.can_view_space', klass=Space).values_list(
                    'pk', flat=True)
                base_qs_filtered_by_pk = base_qs_filtered_by_pk.filter(
                    Q(space_type__is_basic_infrastructure=True) |
                    Q(permitted_groups__in=user.groups.all()) |
                    Q(managed_by=user) |
                    Q(pk__in=explicitly_viewable_pks)
                ).distinct()

            # 对所有非系统管理员/超级管理员用户，默认只显示活跃的空间
            if not user.is_system_admin and not user.is_superuser:
                base_qs_filtered_by_pk = base_qs_filtered_by_pk.filter(is_active=True)

            # 匿名用户进一步过滤
            if not user.is_authenticated:
                base_qs_filtered_by_pk = base_qs_filtered_by_pk.filter(
                    is_active=True,
                    is_bookable=True,
                    space_type__is_basic_infrastructure=True
                )

        return self._apply_eager_loading(base_qs_filtered_by_pk, prefetch_related, select_related).first()

    # 辅助方法：获取 BookableAmenity 的权限过滤 QuerySet，以确保 _update_space_amenities 中的 get_bookable_amenities_for_space 针对管理员权限返回正确的结果
    def get_bookable_amenities_for_space_by_owner(self, space_pk: int, user: CustomUser) -> QuerySet[BookableAmenity]:
        """
        获取指定空间下的所有可预订设施实例的 QuerySet，针对空间所有者或系统管理员。
        此方法旨在被 SpaceService._update_space_amenities 调用。
        """
        from spaces.dao.space_dao import BookableAmenityDAO  # 确保导入 BookableAmenityDAO
        ba_dao = BookableAmenityDAO()
        queryset = ba_dao.get_base_bookable_amenity_queryset().filter(space_id=space_pk)

        if user.is_system_admin:
            # 系统管理员可以看到空间的所有 BA，无论活跃与否
            pass
        elif user.is_space_manager:
            # 空间管理员只能看到其管理空间下的 BA
            # 这里的权限逻辑 `ba.space.managed_by == user` 应该在 `SpaceService._update_space_amenities` 中处理
            # 这里 DAO 层只负责返回“当前用户管理的空间”下的 BA
            # 如果 space 不属于 user 管理，则上层调用者（SpaceService）会进行拒绝
            pass
        else:  # 普通用户不应直接管理 bookable amenities
            return ba_dao.model.objects.none()

        return queryset

    def get_spaces_for_user_management(self, user: CustomUser) -> QuerySet[Space]:
        """
        获取用户有权限管理（例如编辑信息）的空间列表。
        此方法用于特定管理场景，例如在管理员界面列出可管理的空间。
        """
        # 注意：这里返回的 QuerySet 已经包含了 get_base_queryset 的预加载
        # 针对管理，通常只显示活跃且非容器的空间，除非用户有查看禁用项的权限
        return get_objects_for_user(user, 'spaces.can_edit_space_info', klass=self.get_base_queryset()).filter(
            is_active=True).order_by('name')

    def space_has_children(self, space: Space) -> bool:
        """检查给定空间是否有子空间。"""
        return space.child_spaces.exists()

    def space_has_bookings(self, space: Space, BookingModel) -> bool:
        """
        检查给定空间是否有活跃或待处理的预订记录。
        BookingModel 需要作为参数传入以避免循环导入。
        """
        return BookingModel.objects.filter(
            space=space,
            status__in=['PENDING', 'APPROVED', 'CHECKED_IN']
        ).exists() or BookingModel.objects.filter(
            bookable_amenity__space=space,  # 检查该空间下设施的预订
            status__in=['PENDING', 'APPROVED', 'CHECKED_IN']
        ).exists()


class BookableAmenityDAO(BaseDAO):
    """
    BookableAmenity 数据的访问对象。
    提供了获取单个BookableAmenity和按空间获取BookableAmenity列表的方法。
    """
    model = BookableAmenity

    def get_base_bookable_amenity_queryset(self) -> QuerySet[BookableAmenity]:
        """
        获取一个带有常用预加载的基础 BookableAmenity QuerySet。
        """
        return self.model.objects.select_related(
            'space__space_type',
            'amenity'
        )

    def _apply_eager_loading_ba(self, queryset: QuerySet[BookableAmenity], prefetch_related: list = None,
                                select_related: list = None) -> QuerySet[BookableAmenity]:
        """内部辅助方法，用于在BookableAmenity的基础QuerySet之上应用动态的预加载优化。"""
        if select_related:
            queryset = queryset.select_related(*select_related)
        if prefetch_related:
            queryset = queryset.prefetch_related(*prefetch_related)
        return queryset

    def get_bookable_amenity_by_id(self, pk: int, prefetch_related: list = None, select_related: list = None) -> \
            Optional[BookableAmenity]:
        """根据 ID 获取单个可预订设施实例。"""
        return self._apply_eager_loading_ba(
            self.get_base_bookable_amenity_queryset().filter(pk=pk),
            prefetch_related,
            select_related
        ).first()

    def get_bookable_amenities_for_space(self, space_pk: int, user: CustomUser, prefetch_related: list = None,
                                         select_related: list = None) -> QuerySet[BookableAmenity]:
        """
        获取指定空间下的所有可预订设施实例的 QuerySet，并根据用户权限进行过滤。
        """
        queryset = self.get_base_bookable_amenity_queryset().filter(space_id=space_pk).order_by('amenity__name')

        # 对非系统管理员/超级管理员的用户，通常只显示活跃的 BookableAmenity
        is_admin_or_manager = (
                user.is_system_admin or user.is_space_manager
        )
        if not user.is_superuser and not is_admin_or_manager:
            queryset = queryset.filter(is_active=True)

        return self._apply_eager_loading_ba(queryset, prefetch_related, select_related)