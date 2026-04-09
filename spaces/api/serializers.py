# spaces/api/serializers.py
from typing import Any

from rest_framework import serializers
import datetime
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from spaces.models import Amenity, Space, SpaceType, BookableAmenity, \
    CHECK_IN_METHOD_CHOICES

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
    full_name = serializers.CharField(source='get_full_name', read_only=True)

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
# NEW: 用于创建/更新 BookableAmenity 的辅助序列化器
# ====================================================================
class BookableAmenityCreateUpdateSerializer(serializers.Serializer):
    """
    用于在 SpaceCreateUpdateSerializer 中嵌套处理 BookableAmenity 数据的辅助序列化器。
    它接收 amenity_id, quantity, is_bookable, is_active。
    """
    amenity_id = serializers.IntegerField(
        help_text="设施类型的ID",
        error_messages={'required': '设施ID是必填项。', 'invalid': '设施ID必须是整数。'}
    )
    quantity = serializers.IntegerField(
        min_value=0,
        required=False,
        default=1,
        help_text="设施的数量，默认为1",
        error_messages={'min_value': '设施数量不能小于0。', 'invalid': '设施数量必须是整数。'}
    )
    is_bookable = serializers.BooleanField(
        required=False,
        default=True,
        help_text="该设施在该空间中是否可预订，默认为True"
    )
    is_active = serializers.BooleanField(
        required=False,
        default=True,
        help_text="该设施在该空间中是否活跃，默认为True"
    )

    def validate_amenity_id(self, value):
        """验证 amenity_id 是否存在"""
        try:
            Amenity.objects.get(pk=value)
        except Amenity.DoesNotExist:
            raise serializers.ValidationError(f"ID为 {value} 的设施不存在。")
        return value

# ====================================================================
# Space Core Serializers
# ====================================================================

class SpaceBaseSerializer(serializers.ModelSerializer):
    """
    空间基础序列化器，包含所有字段，并新增了**有效预订规则**和**有效签到方式**的计算字段。
    此序列化器需兼容 Django 模型实例和 CachedDictObject 包装的字典数据。
    """
    # 关联字段：改为 SerializerMethodField 以兼容 CachedDictObject 内部的字典结构
    space_type = serializers.SerializerMethodField()
    managed_by = UserMinimalSerializer(read_only=True)
    parent_space = serializers.SerializerMethodField()
    bookable_amenities = serializers.SerializerMethodField()

    permitted_groups = serializers.SerializerMethodField()
    permitted_groups_display = serializers.SerializerMethodField()

    check_in_by = serializers.SerializerMethodField()

    effective_requires_approval = serializers.SerializerMethodField()
    effective_available_start_time = serializers.SerializerMethodField()
    effective_available_end_time = serializers.SerializerMethodField()
    effective_min_booking_duration = serializers.SerializerMethodField()
    effective_max_booking_duration = serializers.SerializerMethodField()
    effective_buffer_time_minutes = serializers.SerializerMethodField()

    effective_check_in_method = serializers.SerializerMethodField()
    effective_check_in_method_display = serializers.SerializerMethodField()

    class Meta:
        model = Space
        fields = [
            'id', 'name', 'location', 'description', 'capacity', 'image',
            'latitude', 'longitude',
            'is_active', 'is_bookable', 'is_container', 'requires_approval',
            'check_in_method',
            'available_start_time', 'available_end_time',
            'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes',
            'created_at', 'updated_at',

            'space_type',
            'managed_by',
            'parent_space',
            'bookable_amenities',
            'permitted_groups',
            'permitted_groups_display',
            'check_in_by',

            'effective_requires_approval',
            'effective_available_start_time',
            'effective_available_end_time',
            'effective_min_booking_duration',
            'effective_max_booking_duration',
            'effective_buffer_time_minutes',
            'effective_check_in_method',
            'effective_check_in_method_display',
        ]
        read_only_fields = fields

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
            space_type_data = obj._data.get('space_type')
            if space_type_data:
                return SpaceTypeMinimalSerializer(space_type_data).data
        elif obj.space_type:
            return SpaceTypeMinimalSerializer(obj.space_type).data
        return None

    def get_parent_space(self, obj: Any) -> dict | None:
        """序列化 parent_space 字段"""
        if self._is_cached_dict_object(obj):
            parent_space_data = obj._data.get('parent_space')
            if parent_space_data:
                return {'id': parent_space_data.get('id'), 'name': parent_space_data.get('name')}
        elif obj.parent_space:
            return {'id': obj.parent_space.id, 'name': obj.parent_space.name}
        return None

    def get_bookable_amenities(self, obj: Any) -> list[dict]:
        """序列化 bookable_amenities 字段"""
        if self._is_cached_dict_object(obj):
            amenities_data = obj._data.get('bookable_amenities')
            if amenities_data:
                return BookableAmenitySerializer(amenities_data, many=True, context=self.context).data
        elif obj.bookable_amenities.exists():
            return BookableAmenitySerializer(obj.bookable_amenities.all(), many=True, context=self.context).data
        return []

    def get_permitted_groups(self, obj: Any) -> list[int]:
        """根据对象类型（模型实例或缓存字典）返回允许访问的用户组ID列表。"""
        if self._is_cached_dict_object(obj):
            return obj._data.get('permitted_groups', [])
        else:
            return list(obj.permitted_groups.all().values_list('pk', flat=True))

    def get_permitted_groups_display(self, obj: Any) -> str:
        group_pks = self.get_permitted_groups(obj)
        if group_pks:
            groups = Group.objects.filter(pk__in=group_pks).values_list('name', flat=True)
            return ", ".join(groups)

        is_basic_infrastructure = False
        if self._is_cached_dict_object(obj):
            space_type_data = obj._data.get('space_type')
            if space_type_data and 'is_basic_infrastructure' in space_type_data:
                is_basic_infrastructure = space_type_data['is_basic_infrastructure']
        elif obj.space_type:
            is_basic_infrastructure = obj.space_type.is_basic_infrastructure

        if is_basic_infrastructure:
            return "所有人"
        return "无特定限制 (需权限)"

    def get_check_in_by(self, obj: Any) -> list[dict]:
        """根据对象类型（模型实例或缓存字典）返回可签到用户的最小信息列表。"""
        if self._is_cached_dict_object(obj):
            user_pks = obj._data.get('check_in_by', [])
        else:
            user_pks = list(obj.check_in_by.all().values_list('pk', flat=True))

        if user_pks:
            users = CustomUser.objects.filter(pk__in=user_pks)
            return UserMinimalSerializer(users, many=True).data
        return []

    def _get_effective_field_value(
            self, obj: Any, field_name: str, default_field_name: str, default_value_if_no_spacetype: Any = None
    ) -> Any:
        """
        从 Space 实例或 CachedDictObject 中获取有效属性值。
        优先级：Space 自身设置 > SpaceType 默认设置 > 兜底默认值。
        """
        space_val = self._get_val_from_obj_or_cached_dict(obj, field_name)
        if space_val is not None and space_val != '':
            return space_val

        space_type_data = None
        if self._is_cached_dict_object(obj):
            space_type_data = obj._data.get('space_type')
        elif obj.space_type:
            space_type_data = obj.space_type.to_dict() if hasattr(obj.space_type, 'to_dict') else obj.space_type

        if space_type_data:
            spacetype_val = self._get_val_from_obj_or_cached_dict(space_type_data, default_field_name) if isinstance(
                space_type_data, dict) else getattr(space_type_data, default_field_name, None)
            if spacetype_val is not None and spacetype_val != '':
                return spacetype_val

        return default_value_if_no_spacetype

    def get_effective_requires_approval(self, obj: Any) -> bool:
        return self._get_effective_field_value(obj, 'requires_approval', 'default_requires_approval', False)

    def get_effective_available_start_time(self, obj: Any) -> str | None:
        time_val = self._get_effective_field_value(obj, 'available_start_time', 'default_available_start_time')
        if isinstance(time_val, datetime.time):
            return time_val.strftime('%H:%M:%S')
        return time_val

    def get_effective_available_end_time(self, obj: Any) -> str | None:
        time_val = self._get_effective_field_value(obj, 'available_end_time', 'default_available_end_time')
        if isinstance(time_val, datetime.time):
            return time_val.strftime('%H:%M:%S')
        return time_val

    def get_effective_min_booking_duration(self, obj: Any) -> str | None:
        duration_val = self._get_effective_field_value(obj, 'min_booking_duration', 'default_min_booking_duration')
        if isinstance(duration_val, datetime.timedelta):
            return str(duration_val)
        return duration_val

    def get_effective_max_booking_duration(self, obj: Any) -> str | None:
        duration_val = self._get_effective_field_value(obj, 'max_booking_duration', 'default_max_booking_duration')
        if isinstance(duration_val, datetime.timedelta):
            return str(duration_val)
        return duration_val

    def get_effective_buffer_time_minutes(self, obj: Any) -> int | None:
        return self._get_effective_field_value(obj, 'buffer_time_minutes', 'default_buffer_time_minutes', 0)

    def get_effective_check_in_method(self, obj: Any) -> str:
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
            'is_container', 'is_bookable', 'is_active', 'requires_approval',

            'space_type',
            'managed_by',

            'permitted_groups',
            'permitted_groups_display',
            'check_in_by',

            'effective_requires_approval',
            'effective_available_start_time',
            'effective_available_end_time',
            'effective_min_booking_duration',
            'effective_max_booking_duration',
            'effective_buffer_time_minutes',
            'effective_check_in_method',
            'effective_check_in_method_display',
        ]

class SpaceCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间创建和更新序列化器。
    """
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

    # --- 关键修改：amenities_data 替代 amenity_ids ---
    amenities_data = serializers.ListField(
        child=BookableAmenityCreateUpdateSerializer(), # 使用新的辅助序列化器
        write_only=True,
        required=False,
        allow_empty=True,
        help_text="以对象列表形式传入设施数据，例如: [{'amenity_id': 1, 'quantity': 2, 'is_bookable': true}]"
    )

    permitted_groups = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), many=True,
        required=False,
        help_text="可预订用户组的ID列表，例如: [1, 2]"
    )

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
            'check_in_method',
            'space_type', 'parent_space', 'managed_by', 'permitted_groups',
            'amenities_data', # 使用新的字段
            'check_in_by',
        ]
        read_only_fields = ('id',)
        extra_kwargs = {
            'min_booking_duration': {'allow_null': True},
            'max_booking_duration': {'allow_null': True},
            'buffer_time_minutes': {'allow_null': True},
            'image': {'required': False, 'allow_null': True},
            'check_in_method': {'required': False, 'allow_null': True, 'allow_blank': True},
            'latitude': {'required': False, 'allow_null': True},
            'longitude': {'required': False, 'allow_null': True},
            'managed_by': {'required': False, 'allow_null': True},
            'permitted_groups': {'required': False},
            'check_in_by': {'required': False},
        }

    def validate(self, data):
        """
        序列化器层面的自定义验证。
        """
        instance = self.instance

        is_active_new = data.get('is_active', instance.is_active if instance else True)
        is_bookable_new = data.get('is_bookable', instance.is_bookable if instance else True)
        is_container_new = data.get('is_container', instance.is_container if instance else False)

        start_time_new = data.get('available_start_time', instance.available_start_time if instance else None)
        end_time_new = data.get('available_end_time', instance.available_end_time if instance else None)

        space_type_new = data.get('space_type', instance.space_type if instance else None)
        parent_space_new = data.get('parent_space', instance.parent_space if instance else None)

        # --- 移除 amenity_ids 的严格验证 ---
        # amenities_data = data.get('amenities_data', None)
        # if amenities_data is not None:
        #     # 移除了对 is_bookable_individually=False 的设施不能被添加的限制
        #     # 现在允许添加所有设施，并由前端控制其在当前空间中是否可预订
        #     pass

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

        return data

    def create(self, validated_data):
        user = self.context['request'].user

        # --- 关键修改：获取 amenities_data ---
        amenities_data = validated_data.pop('amenities_data', [])
        permitted_groups_instances = validated_data.pop('permitted_groups', [])
        check_in_by_instances = validated_data.pop('check_in_by', [])
        managed_by_instance = validated_data.pop('managed_by', None)

        service_result = SpaceService().create_space(
            user=user,
            space_data=validated_data,
            permitted_groups_data=permitted_groups_instances,
            amenities_data=amenities_data, # 传递 amenities_data
            check_in_by_data=check_in_by_instances,
            managed_by_data=managed_by_instance
        )

        if service_result.success:
            return Space.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()

    def update(self, instance, validated_data):
        user = self.context['request'].user

        # --- 关键修改：获取 amenities_data ---
        amenities_data = validated_data.pop('amenities_data', None)
        permitted_groups_instances = validated_data.pop('permitted_groups', None)
        check_in_by_instances = validated_data.pop('check_in_by', None)
        managed_by_instance = validated_data.pop('managed_by', None)

        service_result = SpaceService().update_space(
            user=user,
            pk=instance.pk,
            space_data=validated_data,
            permitted_groups_data=permitted_groups_instances,
            amenities_data=amenities_data, # 传递 amenities_data
            check_in_by_data=check_in_by_instances,
            managed_by_data=managed_by_instance
        )

        if service_result.success:
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