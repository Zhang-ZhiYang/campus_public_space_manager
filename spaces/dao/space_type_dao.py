# spaces/dao/space_type_dao.py
from django.db.models import QuerySet
from spaces.models import SpaceType
from core.dao import BaseDAO
from typing import Optional


class SpaceTypeDAO(BaseDAO):
    """
    SpaceType 数据的访问对象。
    提供了获取所有SpaceType和根据ID获取单个SpaceType的方法。
    """
    model = SpaceType

    def get_queryset(self) -> QuerySet[SpaceType]:
        """
        获取SpaceType的基础QuerySet。
        """
        return super().get_queryset()  # BaseDAO's get_queryset should return self.model.objects

    def _apply_eager_loading(self, queryset: QuerySet[SpaceType], prefetch_related: list = None,
                             select_related: list = None) -> QuerySet[SpaceType]:
        """内部辅助方法，用于在基础QuerySet之上应用动态的预加载优化。"""
        if select_related:
            queryset = queryset.select_related(*select_related)
        if prefetch_related:
            queryset = queryset.prefetch_related(*prefetch_related)
        return queryset

    def get_all(self, prefetch_related: list = None, select_related: list = None) -> QuerySet[SpaceType]:
        """
        获取所有 SpaceType 对象的 QuerySet。
        SpaceType 通常对所有用户可见，无需复杂权限过滤。
        """
        queryset = self.get_queryset().order_by('name')
        return self._apply_eager_loading(queryset, prefetch_related, select_related)

    def get_by_id(self, pk: int, prefetch_related: list = None, select_related: list = None) -> Optional[SpaceType]:
        """
        根据 ID 获取单个 SpaceType 对象。
        """
        queryset = self.get_queryset().filter(pk=pk)
        return self._apply_eager_loading(queryset, prefetch_related, select_related).first()