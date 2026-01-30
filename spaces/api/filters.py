# spaces/filters.py
import django_filters
from django.contrib.auth import get_user_model
from django.db.models import Q
from spaces.models import Space, Amenity, SpaceType # 确保 SpaceType 也被导入
from django.contrib.auth.models import Group # 导入 Group 模型

class SpaceFilter(django_filters.FilterSet):
    """
    Spaces模型的高级过滤器。
    支持通过名称、位置、描述进行模糊搜索；
    通过容量范围、状态、空间类型、管理者、父空间、设施属性和预订规则等多种条件进行过滤。
    """
    # ====== 通用文本搜索 ======
    search = django_filters.CharFilter(method='filter_search', label="搜索 (名称, 位置, 描述)")

    # ====== 容量范围过滤 ======
    min_capacity = django_filters.NumberFilter(field_name='capacity', lookup_expr='gte', label="最小容量")
    max_capacity = django_filters.NumberFilter(field_name='capacity', lookup_expr='lte', label="最大容量")

    # ====== 每日可预订时间过滤 (TimeField) ======
    # 查找最早可预订时间晚于/早于某个时间点
    available_start_time_after = django_filters.TimeFilter(
        field_name='available_start_time', lookup_expr='gte', label="最早可预订时间 (晚于或等于)"
    )
    available_start_time_before = django_filters.TimeFilter(
        field_name='available_start_time', lookup_expr='lte', label="最早可预订时间 (早于或等于)"
    )
    # 查找最晚可预订时间晚于/早于某个时间点
    available_end_time_after = django_filters.TimeFilter(
        field_name='available_end_time', lookup_expr='gte', label="最晚可预订时间 (晚于或等于)"
    )
    available_end_time_before = django_filters.TimeFilter(
        field_name='available_end_time', lookup_expr='lte', label="最晚可预订时间 (早于或等于)"
    )

    # ====== 预订时长过滤 (DurationField - 转换为秒) ======
    # 最短预订时长
    min_booking_duration_seconds_gte = django_filters.NumberFilter(
        field_name='min_booking_duration', lookup_expr='gte', label="最短预订时长 (秒, 大于等于)"
    )
    min_booking_duration_seconds_lte = django_filters.NumberFilter(
        field_name='min_booking_duration', lookup_expr='lte', label="最短预订时长 (秒, 小于等于)"
    )
    # 最长预订时长
    max_booking_duration_seconds_gte = django_filters.NumberFilter(
        field_name='max_booking_duration', lookup_expr='gte', label="最长预订时长 (秒, 大于等于)"
    )
    max_booking_duration_seconds_lte = django_filters.NumberFilter(
        field_name='max_booking_duration', lookup_expr='lte', label="最长预订时长 (秒, 小于等于)"
    )

    # ====== 缓冲时间过滤 (PositiveIntegerField) ======
    buffer_time_minutes_gte = django_filters.NumberFilter(
        field_name='buffer_time_minutes', lookup_expr='gte', label="最小缓冲时间 (分钟, 大于等于)"
    )
    buffer_time_minutes_lte = django_filters.NumberFilter(
        field_name='buffer_time_minutes', lookup_expr='lte', label="最大缓冲时间 (分钟, 小于等于)"
    )

    # ====== 设施相关过滤 (通过 BookableAmenity 链接到 Amenity) ======
    # 查找包含特定设施类型ID的空间 (OR关系，只要有一个匹配即可)
    amenity_ids = django_filters.ModelMultipleChoiceFilter(
        queryset=Amenity.objects.all(),
        field_name='bookable_amenities__amenity', # 跨 BookableAmenity 的 amenity
        to_field_name='id', # 接收 Amenity 的 ID
        conjoined=False,
        label="包含设施类型 (ID)"
    )
    # 查找包含**任何**活跃设施实例的空间
    has_active_amenity_instance = django_filters.BooleanFilter(
        field_name='bookable_amenities__is_active', label="包含启用设施实例"
    )
    # 查找包含**任何**可预订设施实例的空间
    has_bookable_amenity_instance = django_filters.BooleanFilter(
        field_name='bookable_amenities__is_bookable', label="包含可预订设施实例"
    )
    # 查找包含特定设施类型且其数量达到最小值限制的空间
    # 例如：?amenity_ids=1&min_amenity_quantity=2 查找包含投影仪(id=1)且投影仪数量至少为2的空间
    # 注意：这会查找 *任何* amenity_ids 列表中指定的设施中，有 *至少一个* 满足 quantity 的。
    min_amenity_quantity = django_filters.NumberFilter(
        field_name='bookable_amenities__quantity', lookup_expr='gte', label="设施类型最小数量"
    )

    # ====== 用户组权限过滤 ======
    permitted_group_ids = django_filters.ModelMultipleChoiceFilter(
        queryset=Group.objects.all(),
        field_name='permitted_groups',
        to_field_name='id',
        conjoined=False,
        label="允许访问的用户组 (ID)"
    )

    # ====== 空间类型相关过滤 ======
    # 根据空间类型是否为基础型基础设施进行筛选
    space_type_is_basic_infrastructure = django_filters.BooleanFilter(
        field_name='space_type__is_basic_infrastructure', label="空间类型是否为基础型基础设施"
    )
    # 通过 SpaceType 的 ID 过滤
    space_type_id = django_filters.ModelChoiceFilter(
        queryset=SpaceType.objects.all(),
        field_name='space_type',
        to_field_name='id',
        label="空间类型 (ID)"
    )

    # ====== 管理人员相关过滤 ======
    managed_by_id = django_filters.ModelChoiceFilter(
        queryset=get_user_model().objects.all(),
        field_name='managed_by',
        to_field_name='id',
        label="主要管理人员 (ID)"
    )
    # 查找是否无人管理 (managed_by 为空) 的空间
    managed_by_isnull = django_filters.BooleanFilter(
        field_name='managed_by', lookup_expr='isnull', label="是否无主要管理人员"
    )

    # NEW: 签到员过滤
    check_in_by_ids = django_filters.ModelMultipleChoiceFilter(
        queryset=get_user_model().objects.all(),
        field_name='check_in_by',
        to_field_name='id',
        conjoined=False,
        label="可签到人员 (ID)"
    )

    # ====== 日期时间过滤 ======
    created_before = django_filters.DateTimeFilter(field_name='created_at', lookup_expr='lte', label="创建于之前")
    created_after = django_filters.DateTimeFilter(field_name='created_at', lookup_expr='gte', label="创建于之后")
    updated_before = django_filters.DateTimeFilter(field_name='updated_at', lookup_expr='lte', label="更新于之前")
    updated_after = django_filters.DateTimeFilter(field_name='updated_at', lookup_expr='gte', label="更新于之后")

    class Meta:
        model = Space
        # 这些字段可以用于简单的精确/范围过滤，如果上面没有更复杂的自定义Filter定义
        fields = [
            'is_active',         # 布尔值：是否启用
            'is_bookable',       # 布尔值：是否可预订
            'requires_approval', # 布尔值：是否需要审批
            'is_container',      # 布尔值：是否为容器空间
            'parent_space',      # 外键：父级空间的ID
            'latitude',
            'longitude',
        ]

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