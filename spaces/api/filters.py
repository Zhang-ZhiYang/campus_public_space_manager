# spaces/api/filters.py
import django_filters
from django.db.models import Q
from spaces.models import Space, SpaceType, Amenity, Group

class SpaceFilter(django_filters.FilterSet):
    """
    Spaces模型的高级过滤器。
    - 使用 Meta.fields 来处理所有简单的、直接映射到模型字段的过滤。
    - 仅将复杂的、需要自定义逻辑的过滤器（如 search）单独定义。
    """
    # ====== 复杂/自定义的过滤器放这里 ======
    search = django_filters.CharFilter(method='filter_search', label="搜索 (名称, 位置, 描述)")

    # 容量范围过滤 (作为自定义字段的例子)
    min_capacity = django_filters.NumberFilter(field_name='capacity', lookup_expr='gte')
    max_capacity = django_filters.NumberFilter(field_name='capacity', lookup_expr='lte')

    # NEW: 添加一个用于筛选顶级空间的过滤器
    # 当 is_top_level_space=true 时，会筛选 parent_space 为 NULL 的空间
    is_top_level_space = django_filters.BooleanFilter(field_name='parent_space', lookup_expr='isnull', label="是否为顶级空间")

    class Meta:
        model = Space
        # ====== 【核心】所有简单的过滤器都定义在这里 ======
        # 这种方式最标准，能自动处理外键、布尔值等类型的过滤
        fields = {
            'space_type': ['exact'],  # 允许 ?space_type=2
            'parent_space': ['exact'],  # 允许 ?parent_space=10
            'is_active': ['exact'],  # 允许 ?is_active=true
            'is_bookable': ['exact'],
            'is_container': ['exact'], # 允许 ?is_container=true
            'requires_approval': ['exact'],
            'managed_by': ['exact'],  # 允许 ?managed_by=5 (用户ID)

            # 如果需要对更多字段进行简单过滤，直接在这里添加即可
            # 'location': ['icontains'],      # 允许 ?location__icontains=Campus
        }

    def filter_search(self, queryset, name, value):
        """
        组合搜索，可以在 name, location, description 字段中进行模糊匹配。
        """
        if value:
            return queryset.filter(
                Q(name__icontains=value) |
                Q(location__icontains=value) |
                Q(description__icontains=value)
            ).distinct()
        return queryset