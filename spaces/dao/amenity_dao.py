# spaces/dao/amenity_dao.py
from django.db.models import QuerySet
from spaces.models import Amenity
from core.dao import BaseDAO
from typing import Optional

class AmenityDAO(BaseDAO):
    """
    Amenity 数据的访问对象。
    提供了获取所有Amenity和根据ID获取单个Amenity的方法。
    """
    model = Amenity

    def get_queryset(self) -> QuerySet[Amenity]:
        """
        获取Amenity的基础QuerySet。
        """
        return super().get_queryset() # BaseDAO's get_queryset should return self.model.objects

    def _apply_eager_loading(self, queryset: QuerySet[Amenity], prefetch_related: list = None, select_related: list = None) -> QuerySet[Amenity]:
        """内部辅助方法，用于在基础QuerySet之上应用动态的预加载优化。"""
        if select_related:
            queryset = queryset.select_related(*select_related)
        if prefetch_related:
            queryset = queryset.prefetch_related(*prefetch_related)
        return queryset

    def get_all(self, prefetch_related: list = None, select_related: list = None) -> QuerySet[Amenity]:
        """
        获取所有 Amenity 对象的 QuerySet。
        Amenity 类型通常对所有用户可见，无需复杂权限过滤。
        """
        queryset = self.get_queryset().order_by('name')
        return self._apply_eager_loading(queryset, prefetch_related, select_related)

    def get_by_id(self, pk: int, prefetch_related: list = None, select_related: list = None) -> Optional[Amenity]:
        """
        根据 ID 获取单个 Amenity 对象。
        """
        queryset = self.get_queryset().filter(pk=pk)
        return self._apply_eager_loading(queryset, prefetch_related, select_related).first()