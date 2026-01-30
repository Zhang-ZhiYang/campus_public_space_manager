# spaces/api/serializers.py
from typing import Any

from rest_framework import serializers
import datetime
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from spaces.models import Amenity, Space, SpaceType, BookableAmenity, \
    CHECK_IN_METHOD_CHOICES  # <--- 导入 CHECK_IN_METHOD_CHOICES

# 确保导入 Service 层
from spaces.service.space_service import SpaceService
from spaces.service.amenity_service import AmenityService
from spaces.service.space_type_service import SpaceTypeService

CustomUser = get_user_model()

# ====================================================================
# 辅助序列化器 (根据需要调整，确保与您的实际 User 模型匹配)
# ====================================================================
# 用户最小信息序列化器
class UserMinimalSerializer(serializers.ModelSerializer):
    """
    用于嵌套显示用户关键信息的最小序列化器。
    """
    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    full_name = serializers.CharField(source='get_full_name', read_only=True)  # 假设 CustomUser 有 get_full_name 方法

    class Meta:
        model = CustomUser
        fields = ('id', 'username', 'full_name')

# 空间类型最小信息序列化器
class SpaceTypeMinimalSerializer(serializers.ModelSerializer):
    """
    用于嵌套显示空间类型关键信息的最小序列化器。
    """
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)

    class Meta:
        model = SpaceType
        fields = ('id', 'name')

# 设施序列化器（不变）
class AmenitySerializer(serializers.ModelSerializer):
    """
    设施序列化器，用于在Space详情中嵌套显示和设施的CRUD操作。
    """

    class Meta:
        model = Amenity
        fields = ['id', 'name', 'description', 'is_bookable_individually']
        read_only_fields = ['id']

# 可预订设施实例序列化器
class BookableAmenitySerializer(serializers.ModelSerializer):
    """
    可预订设施实例的序列化器，用于 Space 详情页的嵌套显示。
    """
    amenity = AmenitySerializer(read_only=True)

    class Meta:
        model = BookableAmenity
        fields = ['id', 'amenity', 'quantity', 'is_bookable', 'is_active']
        read_only_fields = ['id']

# 用户组最小序列化器（如果需要嵌套显示 Group 对象，则启用）
class GroupMinimalSerializer(serializers.ModelSerializer):
    """
    用于嵌套显示用户组关键信息的最小序列化器。
    """

    class Meta:
        model = Group
        fields = ['id', 'name']

# ====================================================================
# Space Core Serializers (核心重构部分)
# ====================================================================

class SpaceBaseSerializer(serializers.ModelSerializer):
    """
    空间基础序列化器，包含所有字段，并新增了**有效预订规则**和**有效签到方式**的计算字段。
    此序列化器需兼容 Django 模型实例和 CachedDictObject 包装的字典数据。
    """
    # 关联字段：改为 SerializerMethodField 以兼容 CachedDictObject 内部的字典结构
    space_type = serializers.SerializerMethodField()
    managed_by = UserMinimalSerializer(read_only=True)  # managed_by 直接是 CustomUser.to_dict_minimal() 的结果
    parent_space = serializers.SerializerMethodField()
    bookable_amenities = serializers.SerializerMethodField()

    # 关键修改：permitted_groups 改为 SerializerMethodField
    permitted_groups = serializers.SerializerMethodField()
    # 用于显示 `permitted_groups` 的友好字符串
    permitted_groups_display = serializers.SerializerMethodField()

    # NEW: 添加 check_in_by 字段
    check_in_by = serializers.SerializerMethodField()

    # 有效预订规则计算字段 (保留为 SerializerMethodField)
    effective_requires_approval = serializers.SerializerMethodField()
    effective_available_start_time = serializers.SerializerMethodField()
    effective_available_end_time = serializers.SerializerMethodField()
    effective_min_booking_duration = serializers.SerializerMethodField()
    effective_max_booking_duration = serializers.SerializerMethodField()
    effective_buffer_time_minutes = serializers.SerializerMethodField()

    # 有效签到方式及显示名称 (新增为 SerializerMethodField)
    effective_check_in_method = serializers.SerializerMethodField()
    effective_check_in_method_display = serializers.SerializerMethodField()

    class Meta:
        model = Space
        fields = [
            'id', 'name', 'location', 'description', 'capacity', 'image',
            'latitude', 'longitude',
            'is_active', 'is_bookable', 'is_container', 'requires_approval',
            'check_in_method',  # 空间自身设置的签到方式 (直接从模型/字典获取)
            'available_start_time', 'available_end_time',  # 空间自身设置的可用时间
            'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes',  # 空间自身设置的预订时长
            'created_at', 'updated_at',

            # 关联字段 (现在通过 SerializerMethodField 处理)
            'space_type',
            'managed_by',
            'parent_space',
            'bookable_amenities',
            # 关键修改：这里引用的是 SerializerMethodField 的 permitted_groups
            'permitted_groups',
            'permitted_groups_display',
            'check_in_by', # NEW: 添加 check_in_by 字段

            # 有效预订规则和签到方式 (通过 SerializerMethodField 计算或获取)
            'effective_requires_approval',
            'effective_available_start_time',
            'effective_available_end_time',
            'effective_min_booking_duration',
            'effective_max_booking_duration',
            'effective_buffer_time_minutes',
            'effective_check_in_method',
            'effective_check_in_method_display',
        ]
        read_only_fields = fields  # Made all fields read-only for SpaceBaseSerializer

    def _is_cached_dict_object(self, obj: Any) -> bool:
        """检查对象是否为 CachedDictObject"""
        return hasattr(obj, '_data') and isinstance(obj._data, dict)

    def _get_val_from_obj_or_cached_dict(self, obj: Any, field_name: str, default_if_none: Any = None) -> Any:
        """从模型实例或 CachedDictObject 中安全地获取字段值"""
        if self._is_cached_dict_object(obj):
            val = obj._data.get(field_name)
        else:
            val = getattr(obj, field_name, None)
        return val if val is not None else default_if_none

    def get_space_type(self, obj: Any) -> dict | None:
        """序列化 space_type 字段"""
        if self._is_cached_dict_object(obj):
            # CachedDictObject 内部 space_type 已经是字典
            space_type_data = obj._data.get('space_type')
            if space_type_data:
                return SpaceTypeMinimalSerializer(space_type_data).data
        elif obj.space_type:
            # 模型实例
            return SpaceTypeMinimalSerializer(obj.space_type).data
        return None

    def get_parent_space(self, obj: Any) -> dict | None:
        """序列化 parent_space 字段"""
        if self._is_cached_dict_object(obj):
            # CachedDictObject 内部 parent_space 已经是字典
            parent_space_data = obj._data.get('parent_space')
            if parent_space_data:
                # 避免循环引用，只显示 id 和 name
                return {'id': parent_space_data.get('id'), 'name': parent_space_data.get('name')}
        elif obj.parent_space:
            # 模型实例
            return {'id': obj.parent_space.id, 'name': obj.parent_space.name}
        return None

    def get_bookable_amenities(self, obj: Any) -> list[dict]:
        """序列化 bookable_amenities 字段"""
        if self._is_cached_dict_object(obj):
            # CachedDictObject 内部 bookable_amenities 已经是 BookableAmenity 的字典列表
            amenities_data = obj._data.get('bookable_amenities')
            if amenities_data:
                return BookableAmenitySerializer(amenities_data, many=True, context=self.context).data
        elif obj.bookable_amenities.exists():
            # 模型实例
            return BookableAmenitySerializer(obj.bookable_amenities.all(), many=True, context=self.context).data
        return []

    # 关键修改：获取 permitted_groups 的实际 ID 列表
    def get_permitted_groups(self, obj: Any) -> list[int]:
        """根据对象类型（模型实例或缓存字典）返回允许访问的用户组ID列表。"""
        if self._is_cached_dict_object(obj):
            # CachedDictObject 内部 of permitted_groups 已经是 ID 列表
            return obj._data.get('permitted_groups', [])
        else:
            # Django 模型实例，需要从 ManyRelatedManager 中提取 PK
            return list(obj.permitted_groups.all().values_list('pk', flat=True))

    def get_permitted_groups_display(self, obj: Any) -> str:
        """获取可预订用户组的显示字符串"""
        # 现在可以直接调用 get_permitted_groups 来获取 ID 列表
        group_pks = self.get_permitted_groups(obj)

        # 如果有具体的用户组ID，尝试从数据库获取名称
        if group_pks:
            groups = Group.objects.filter(pk__in=group_pks).values_list('name', flat=True)
            return ", ".join(groups)

        # 检查是否是基础型基础设施空间的逻辑
        is_basic_infrastructure = False
        if self._is_cached_dict_object(obj):
            space_type_data = obj._data.get('space_type')
            if space_type_data and 'is_basic_infrastructure' in space_type_data:
                is_basic_infrastructure = space_type_data['is_basic_infrastructure']
        elif obj.space_type:
            is_basic_infrastructure = obj.space_type.is_basic_infrastructure

        if is_basic_infrastructure:
            return "所有人"

        return "无特定限制 (需权限)"  # 默认值

    # NEW: 增加 get_check_in_by 方法
    def get_check_in_by(self, obj: Any) -> list[dict]:
        """根据对象类型（模型实例或缓存字典）返回可签到用户的最小信息列表。"""
        if self._is_cached_dict_object(obj):
            # CachedDictObject 内部 of check_in_by 已经是 ID 列表
            user_pks = obj._data.get('check_in_by', [])
        else:
            # Django 模型实例，需要从 ManyRelatedManager 中提取 PK
            user_pks = list(obj.check_in_by.all().values_list('pk', flat=True))

        if user_pks:
            # 从数据库获取详细的用户信息
            users = CustomUser.objects.filter(pk__in=user_pks)
            return UserMinimalSerializer(users, many=True).data
        return []

    # --- 辅助方法，统一从模型实例或 CachedDictObject 中获取有效字段值 ---
    def _get_effective_field_value(
            self, obj: Any, field_name: str, default_field_name: str, default_value_if_no_spacetype: Any = None
    ) -> Any:
        """
        从 Space 实例或 CachedDictObject 中获取有效属性值。
        优先级：Space 自身设置 > SpaceType 默认设置 > 兜底默认值。
        """
        # 1. 尝试获取 Space 自身的设置
        space_val = self._get_val_from_obj_or_cached_dict(obj, field_name)
        if space_val is not None and space_val != '':  # 检查非 None 且非空字符串
            return space_val

        # 2. 如果 Space 自身为空，尝试获取 SpaceType 的默认设置
        space_type_data = None
        if self._is_cached_dict_object(obj):
            space_type_data = obj._data.get('space_type')  # CachedDictObject 的 space_type 已经是字典
        elif obj.space_type:
            # 模型实例，需要从 space_type 对象中获取
            # 如果 SpaceType 也实现了 to_dict，可以这样获取；否则直接 getattr
            space_type_data = obj.space_type.to_dict() if hasattr(obj.space_type, 'to_dict') else obj.space_type

        if space_type_data:
            spacetype_val = self._get_val_from_obj_or_cached_dict(space_type_data, default_field_name) if isinstance(
                space_type_data, dict) else getattr(space_type_data, default_field_name, None)
            if spacetype_val is not None and spacetype_val != '':
                return spacetype_val

        # 3. 如果 Space 和 SpaceType 都为空，返回兜底默认值
        return default_value_if_no_spacetype

    def get_effective_requires_approval(self, obj: Any) -> bool:
        return self._get_effective_field_value(obj, 'requires_approval', 'default_requires_approval', False)

    def get_effective_available_start_time(self, obj: Any) -> str | None:
        time_val = self._get_effective_field_value(obj, 'available_start_time', 'default_available_start_time')
        # time_val 可能是 time 对象或字符串
        if isinstance(time_val, datetime.time):
            return time_val.strftime('%H:%M:%S')
        return time_val  # 如果已经是字符串或 None

    def get_effective_available_end_time(self, obj: Any) -> str | None:
        time_val = self._get_effective_field_value(obj, 'available_end_time', 'default_available_end_time')
        if isinstance(time_val, datetime.time):
            return time_val.strftime('%H:%M:%S')
        return time_val

    def get_effective_min_booking_duration(self, obj: Any) -> str | None:
        duration_val = self._get_effective_field_value(obj, 'min_booking_duration', 'default_min_booking_duration')
        if isinstance(duration_val, datetime.timedelta):
            return str(duration_val)  # 将 timedelta 转换为字符串
        return duration_val

    def get_effective_max_booking_duration(self, obj: Any) -> str | None:
        duration_val = self._get_effective_field_value(obj, 'max_booking_duration', 'default_max_booking_duration')
        if isinstance(duration_val, datetime.timedelta):
            return str(duration_val)
        return duration_val

    def get_effective_buffer_time_minutes(self, obj: Any) -> int | None:
        return self._get_effective_field_value(obj, 'buffer_time_minutes', 'default_buffer_time_minutes', 0)

    def get_effective_check_in_method(self, obj: Any) -> str:
        # 兜底默认值与 Space 模型 save 方法中的逻辑保持一致
        return self._get_effective_field_value(obj, 'check_in_method', 'default_check_in_method', 'HYBRID')

    def get_effective_check_in_method_display(self, obj: Any) -> str:
        effective_method = self.get_effective_check_in_method(obj)
        return dict(CHECK_IN_METHOD_CHOICES).get(effective_method, '未知')

class SpaceListSerializer(SpaceBaseSerializer):
    """
    空间列表序列化器。
    它继承了 SpaceBaseSerializer，但通过 `Meta.fields` 明确指定在列表视图中展示的字段。
    """

    class Meta(SpaceBaseSerializer.Meta):
        fields = [
            'id', 'name', 'location', 'capacity', 'image',
            'is_active', 'is_bookable', 'is_container',
            # 简化关联字段在列表中的显示
            'is_container', 'is_bookable', 'is_active', 'requires_approval',

            'space_type',  # 最小信息
            'managed_by',  # 最小用户信息

            # permitted_groups 现在通过 SerializerMethodField 处理
            'permitted_groups',
            'permitted_groups_display',  # 显示 Permitted Groups 的友好字符串
            'check_in_by', # NEW: 添加 check_in_by 字段

            # 有效预订规则和签到方式
            'effective_requires_approval',
            'effective_available_start_time',
            'effective_available_end_time',
            'effective_min_booking_duration',
            'effective_max_booking_duration',
            'effective_buffer_time_minutes',
            'effective_check_in_method',
            'effective_check_in_method_display',
            # bookable_amenities 在列表视图中通常不显示，数据量过大
            # 如果需要，在 Meta.fields 中添加 'bookable_amenities'
        ]

class SpaceCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间创建和更新序列化器。
    """
    # Foreign Key 和 ManyToMany 字段使用 PrimaryKeyRelatedField 处理 ID
    space_type = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), write_only=True, required=False, allow_null=True,
        help_text="空间类型的ID，例如：1"
    )
    parent_space = serializers.PrimaryKeyRelatedField(
        queryset=Space.objects.all(), write_only=True, required=False, allow_null=True,
        help_text="父级空间的ID，例如：2"
    )
    managed_by = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), write_only=True, required=False, allow_null=True,
        help_text="主要管理人员的ID，例如：3"
    )

    # amenity_ids 是一个用于创建/更新 BookableAmenity 的辅助字段
    amenity_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        allow_empty=True,
        help_text="以整数列表形式传入设施ID, 例如: [1, 2, 3]"
    )

    # permitted_groups 接受 ID 列表并自动转换为 Group 实例
    permitted_groups = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), many=True,
        required=False,
        help_text="可预订用户组的ID列表，例如: [1, 2]"
    )

    # NEW: 添加 check_in_by 字段，接受用户ID列表
    check_in_by = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), many=True,
        required=False,
        help_text="可为该空间签到的用户ID列表，例如: [4, 5]"
    )

    class Meta:
        model = Space
        fields = [
            'id', 'name', 'location', 'description', 'capacity',
            'latitude', 'longitude',
            'is_bookable', 'is_active', 'is_container', 'requires_approval', 'image',
            'available_start_time', 'available_end_time',
            'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes',
            'check_in_method',  # 允许创建/更新时设置签到方式
            'space_type', 'parent_space', 'managed_by', 'permitted_groups',  # 直接是模型实例
            'amenity_ids',  # 辅助字段
            'check_in_by', # NEW: 添加 check_in_by 字段
        ]
        read_only_fields = ('id',)
        extra_kwargs = {
            'min_booking_duration': {'allow_null': True},
            'max_booking_duration': {'allow_null': True},
            'buffer_time_minutes': {'allow_null': True},
            'image': {'required': False, 'allow_null': True},
            'check_in_method': {'required': False, 'allow_null': True, 'allow_blank': True},
            'latitude': {'required': False, 'allow_null': True},  # <--- 新增
            'longitude': {'required': False, 'allow_null': True},  # <--- 新增
        }

    def validate(self, data):
        """
        序列化器层面的自定义验证。
        此方法需要兼容创建和更新两种场景，通过 self.instance 判断。
        """
        instance = self.instance  # None if creating, Space instance if updating

        # --- 获取当前或即将设置的值 ---
        # 对于非关联字段，直接从 data 中取，如果没有则从 instance 中取 (更新时)
        # 对于关联字段 (space_type, parent_space)，data 中已经是模型实例
        is_active_new = data.get('is_active', instance.is_active if instance else True)
        is_bookable_new = data.get('is_bookable', instance.is_bookable if instance else True)
        is_container_new = data.get('is_container', instance.is_container if instance else False)

        start_time_new = data.get('available_start_time', instance.available_start_time if instance else None)
        end_time_new = data.get('available_end_time', instance.available_end_time if instance else None)

        space_type_new = data.get('space_type', instance.space_type if instance else None)
        parent_space_new = data.get('parent_space', instance.parent_space if instance else None)

        amenity_ids = data.get('amenity_ids', None)  # 仅在数据传入时验证

        # --- 验证逻辑 ---
        # 1. 时间字段一致性验证
        if start_time_new and end_time_new and start_time_new >= end_time_new:
            raise serializers.ValidationError(
                {'available_end_time': '每日最晚可预订时间必须晚于最早可预订时间。'},
                code='invalid_time_range'
            )

        # 2. is_active, is_bookable, is_container 业务规则验证
        if not is_active_new and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': '不活跃的空间不能设置为可预订。'},
                code='inactive_space_not_bookable'
            )

        if is_container_new and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': '容器空间通常不直接预订，请设置 is_bookable 为 False。'},
                code='container_space_not_bookable'
            )

        # 3. SpaceType.default_is_bookable 与 Space.is_bookable 的一致性
        if space_type_new and not space_type_new.default_is_bookable and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': f"所属空间类型 '{space_type_new.name}' 默认不可预订，此空间不能设置为可预订。"},
                code='space_type_not_bookable_conflict'
            )

        # 4. 父级空间不能是自身 (仅在更新时检查，创建时没有 instance.pk)
        if parent_space_new and instance and parent_space_new == instance:
            raise serializers.ValidationError(
                {'parent_space': '空间不能将自身设置为父级空间。'},
                code='self_parent_space'
            )

        # 5. 确保 is_bookable_individually 为 False 的 Amenity 不会尝试单独预订
        if amenity_ids is not None:  # 只有当 amenity_ids 被传入时才检查
            # 检查传入的 amenity_ids 是否包含不可单独预订的设施
            non_bookable_amenities = Amenity.objects.filter(id__in=amenity_ids, is_bookable_individually=False)
            if non_bookable_amenities.exists():
                names = ", ".join([a.name for a in non_bookable_amenities])
                raise serializers.ValidationError(
                    {'amenity_ids': f'以下设施类型不可单独预订，不能作为可预订设施实例添加到空间中: {names}'},
                    code='non_bookable_amenity_added'
                )

        return data

    def create(self, validated_data):
        user = self.context['request'].user

        # 从 validated_data 中分离出非直接模型字段（由 Service 层额外处理的字段）
        amenity_ids = validated_data.pop('amenity_ids', [])
        permitted_groups_instances = validated_data.pop('permitted_groups', [])  # 这是 Group 实例的列表
        check_in_by_instances = validated_data.pop('check_in_by', []) # NEW: 分离 check_in_by 用户实例

        # 调用 Service 层创建空间
        # validated_data 中现在只包含 Space 模型直接对应的字段值（FK 字段已是模型实例）
        service_result = SpaceService().create_space(
            user=user,
            space_data=validated_data,  # 包含所有 Space 模型的字段
            permitted_groups_data=permitted_groups_instances,  # Group 实例列表
            amenity_ids_data=amenity_ids,  # 设施 ID 列表
            check_in_by_data=check_in_by_instances # NEW: 传递 check_in_by 用户实例列表
        )

        if service_result.success:
            # Service 返回的是一个包含新创建空间 ID 的字典。
            # 根据 DRF 的 create 方法约定，这里应该返回实际的模型实例。
            return Space.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()

    def update(self, instance, validated_data):
        user = self.context['request'].user

        # 从 validated_data 中分离出非直接模型字段
        amenity_ids = validated_data.pop('amenity_ids', None)  # None 表示未传入，不更新
        permitted_groups_instances = validated_data.pop('permitted_groups', None)  # None 表示未传入，不更新
        check_in_by_instances = validated_data.pop('check_in_by', None) # NEW: 分离 check_in_by 用户实例

        # 调用 Service 层更新空间
        service_result = SpaceService().update_space(
            user=user,
            pk=instance.pk,
            space_data=validated_data,  # 包含更新的 Space 模型字段
            permitted_groups_data=permitted_groups_instances,
            amenity_ids_data=amenity_ids,
            check_in_by_data=check_in_by_instances # NEW: 传递 check_in_by 用户实例列表
        )

        if service_result.success:
            # Service 返回的仍然是字典，这里同样需要返回实际的模型实例。
            return Space.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()

# (AmenityType Serializers 和 SpaceType Serializers 保持不变)
# --------- Amenity Type Serializers (保持不变) ---------

class AmenityBaseSerializer(serializers.ModelSerializer):
    """
    设施类型（Amenity）的基础序列化器。
    """

    class Meta:
        model = Amenity
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']

class AmenityCreateUpdateSerializer(serializers.ModelSerializer):
    """
    设施类型创建和更新序列化器。
    """

    class Meta:
        model = Amenity
        fields = ['id', 'name', 'description', 'is_bookable_individually']
        read_only_fields = ['id']

    def validate(self, data):
        return data

    def create(self, validated_data):
        user = self.context['request'].user
        service_result = AmenityService().create_amenity(user=user, amenity_data=validated_data)
        if service_result.success:
            return Amenity.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()

    def update(self, instance, validated_data):
        user = self.context['request'].user
        service_result = AmenityService().update_amenity(user=user, pk=instance.pk, amenity_data=validated_data)
        if service_result.success:
            return Amenity.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()

# --------- Space Type Serializers ---------

class SpaceTypeBaseSerializer(serializers.ModelSerializer):
    """
    空间类型（SpaceType）的基础序列化器，包含所有字段。
    """
    default_check_in_method_display = serializers.SerializerMethodField()

    class Meta:
        model = SpaceType
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')

    def get_default_check_in_method_display(self, obj: SpaceType) -> str:
        return dict(CHECK_IN_METHOD_CHOICES).get(obj.default_check_in_method, '未知')

class SpaceTypeCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间类型创建和更新序列化器。
    """

    class Meta:
        model = SpaceType
        fields = '__all__'
        read_only_fields = ('id',)
        extra_kwargs = {
            'default_available_start_time': {'allow_null': True},
            'default_available_end_time': {'allow_null': True},
            'default_min_booking_duration': {'allow_null': True},
            'default_max_booking_duration': {'allow_null': True},
            'default_buffer_time_minutes': {'allow_null': True},
            'default_check_in_method': {'required': False, 'allow_null': False, 'allow_blank': False}
        }

    def validate(self, data):
        instance = self.instance

        start_time_new = data.get('default_available_start_time',
                                  instance.default_available_start_time if instance else None)
        end_time_new = data.get('default_available_end_time', instance.default_available_end_time if instance else None)

        if start_time_new and end_time_new and start_time_new >= end_time_new:
            raise serializers.ValidationError(
                {'default_available_end_time': '默认最晚可预订时间必须晚于最早可预订时间。'},
                code='invalid_default_time_range'
            )
        return data

    def create(self, validated_data):
        user = self.context['request'].user
        service_result = SpaceTypeService().create_space_type(user=user, space_type_data=validated_data)
        if service_result.success:
            return SpaceType.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()

    def update(self, instance, validated_data):
        user = self.context['request'].user
        service_result = SpaceTypeService().update_space_type(user=user, pk=instance.pk, space_type_data=validated_data)
        if service_result.success:
            return SpaceType.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()