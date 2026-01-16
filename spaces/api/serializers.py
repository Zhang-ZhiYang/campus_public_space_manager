# spaces/serializers.py
from typing import Any

from rest_framework import serializers
import datetime
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group


from spaces.models import Amenity, Space, SpaceType, BookableAmenity
from spaces.service.space_service import SpaceService  # Import SpaceService
from spaces.service.amenity_service import AmenityService  # Import AmenityService
from spaces.service.space_type_service import SpaceTypeService  # Import SpaceTypeService

CustomUser = get_user_model()

class UserSerializerMinimal(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='id', read_only=True)
    full_name = serializers.CharField(source='get_full_name', read_only=True)

    class Meta:
        model = CustomUser
        fields = ('user_id', 'username', 'full_name')

class SpaceTypeSerializerMinimal(serializers.ModelSerializer):
    space_type_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = SpaceType
        fields = ('space_type_id', 'name')
        read_only_fields = ('space_type_id', 'name')


class AmenitySerializer(serializers.ModelSerializer):
    """
    设施序列化器，用于在Space详情中嵌套显示和设施的CRUD操作。
    """

    class Meta:
        model = Amenity
        fields = ['id', 'name', 'description', 'is_bookable_individually']
        read_only_fields = ['id']


class BookableAmenitySerializer(serializers.ModelSerializer):
    """
    可预订设施实例的序列化器，用于 Space 详情页的嵌套显示。
    """
    amenity = AmenitySerializer(read_only=True)

    class Meta:
        model = BookableAmenity
        fields = ['id', 'amenity', 'quantity', 'is_bookable', 'is_active']
        read_only_fields = ['id']


class SpaceBaseSerializer(serializers.ModelSerializer):
    """
    空间基础序列化器，包含所有字段，并新增了**有效预订规则**的计算字段。
    """
    # These will be instances from DRF's default behavior, or from to_dict()
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    managed_by = UserSerializerMinimal(read_only=True)
    bookable_amenities = BookableAmenitySerializer(many=True, read_only=True)
    permitted_groups_display = serializers.SerializerMethodField()

    effective_requires_approval = serializers.SerializerMethodField()
    effective_available_start_time = serializers.SerializerMethodField()
    effective_available_end_time = serializers.SerializerMethodField()
    effective_min_booking_duration = serializers.SerializerMethodField()
    effective_max_booking_duration = serializers.SerializerMethodField()
    effective_buffer_time_minutes = serializers.SerializerMethodField()

    class Meta:
        model = Space
        fields = '__all__'  # Use __all__ will include all model fields and SerializerMethodField
        read_only_fields = ('id', 'created_at', 'updated_at')  # Add these as read-only

    def get_permitted_groups_display(self,
                                     obj: Any) -> str:  # Use Any for obj to handle Model instance or CachedDictObject
        # obj could be a Model instance or a CachedDictObject (which is dict-like)
        if hasattr(obj, '_data') and isinstance(obj._data, dict):  # Check for CachedDictObject
            space_data = obj._data  # Use direct reference to the dictionary

            # If `permitted_groups` explicitly exist in the cached data and it's not empty,
            # we prioritize displaying them.
            if 'permitted_groups' in space_data and space_data['permitted_groups']:
                group_pks = set(space_data['permitted_groups'])
                # Fetch Group names from DB as only PKs are in cached dict
                groups = Group.objects.filter(pk__in=group_pks).values_list('name', flat=True)
                return ", ".join(groups)

            # If no explicit permitted_groups, determine display based on space_type.is_basic_infrastructure
            # FIX: Safely get space_type dict, defaulting to an empty dict if space_type is None or missing.
            # This handles the case where space_type in _data is explicitly None.
            space_type_info = space_data.get('space_type') or {}
            is_basic_infrastructure = space_type_info.get('is_basic_infrastructure', False)

            if is_basic_infrastructure:
                return "所有人"  # Display if basic infrastructure
            return "无特定限制 (需权限)"  # Default if not basic infrastructure and no specific groups

        # Original logic for model instance directly
        if obj.permitted_groups.exists():
            return ", ".join([group.name for group in obj.permitted_groups.all()])

        if obj.space_type and obj.space_type.is_basic_infrastructure:
            return "所有人"
        return "无特定限制 (需权限)"

    def _get_field_value_from_obj(self, obj: Any, field_name: str, default_field_name: str,
                                  default_value_if_no_spacetype=None):
        if hasattr(obj, '_data') and isinstance(obj._data, dict):  # Handle CachedDictObject
            obj_val = obj._data.get(field_name)
            if obj_val is not None:
                return obj_val

            # FIX: Safely get space_type_data, defaulting to an empty dict if space_type is None or missing.
            space_type_data = obj._data.get('space_type') or {}

            if space_type_data.get(default_field_name) is not None:
                return space_type_data.get(default_field_name)
            return default_value_if_no_spacetype

        # Original logic for model instance
        obj_val = getattr(obj, field_name, None)
        if obj_val is not None:
            return obj_val
        spacetype = obj.space_type
        if spacetype and getattr(spacetype, default_field_name, None) is not None:
            return getattr(spacetype, default_field_name)
        return default_value_if_no_spacetype

    def get_effective_requires_approval(self, obj: Any) -> bool:
        return self._get_field_value_from_obj(obj, 'requires_approval', 'default_requires_approval', False)

    def get_effective_available_start_time(self, obj: Any) -> str | None:
        time_obj = self._get_field_value_from_obj(obj, 'available_start_time', 'default_available_start_time')
        if isinstance(time_obj, (str, bytes)):  # Already string from cache
            return time_obj
        return time_obj.strftime('%H:%M:%S') if time_obj else None  # datetime.time object

    def get_effective_available_end_time(self, obj: Any) -> str | None:
        time_obj = self._get_field_value_from_obj(obj, 'available_end_time', 'default_available_end_time')
        if isinstance(time_obj, (str, bytes)):
            return time_obj
        return time_obj.strftime('%H:%M:%S') if time_obj else None

    def get_effective_min_booking_duration(self, obj: Any) -> str | None:
        duration_obj = self._get_field_value_from_obj(obj, 'min_booking_duration', 'default_min_booking_duration')
        if isinstance(duration_obj, (str, bytes)):  # Already string from cache
            return duration_obj
        # Ensure DurationField is stringified consistently
        return str(duration_obj) if duration_obj is not None else None  # `is not None` is more precise

    def get_effective_max_booking_duration(self, obj: Any) -> str | None:
        duration_obj = self._get_field_value_from_obj(obj, 'max_booking_duration', 'default_max_booking_duration')
        if isinstance(duration_obj, (str, bytes)):
            return duration_obj
        # Ensure DurationField is stringified consistently
        return str(duration_obj) if duration_obj is not None else None

    def get_effective_buffer_time_minutes(self, obj: Any) -> int | None:
        return self._get_field_value_from_obj(obj, 'buffer_time_minutes', 'default_buffer_time_minutes', 0)


class SpaceListSerializer(SpaceBaseSerializer):
    """
    空间列表序列化器。
    它继承了 SpaceBaseSerializer，因此会自动包含所有模型字段及 `effective_` 字段。
    这里的 Meta.fields 应该明确列出你想在列表视图中展示的字段。
    """

    class Meta(SpaceBaseSerializer.Meta):
        fields = [
            'id', 'name', 'location', 'capacity', 'image', 'description',
            'is_active', 'is_bookable', 'is_container',
            'space_type', 'managed_by',
            'permitted_groups_display',
            'effective_requires_approval',
            'effective_available_start_time',
            'effective_available_end_time',
            'effective_min_booking_duration',
            'effective_max_booking_duration',
            'effective_buffer_time_minutes',
            'bookable_amenities',
        ]


class SpaceCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间创建和更新序列化器。
    """
    # These fields using `source='...'` will ensure `validated_data` contains model instances, not just PKs.
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

    amenity_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        allow_empty=True,
        help_text="以整数列表形式传入设施ID, 例如: [1, 2, 3]"
    )

    # This field already correctly resolves to Group instances thanks to `many=True` and `PrimaryKeyRelatedField`
    permitted_groups = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), many=True,
        required=False,
        help_text="可预订用户组的ID列表，例如: [1, 2]"
    )

    class Meta:
        model = Space
        fields = [
            'id', 'name', 'location', 'description', 'capacity',
            'is_bookable', 'is_active', 'is_container', 'requires_approval', 'image',
            'available_start_time', 'available_end_time',
            'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes',
            'space_type', 'parent_space', 'managed_by', 'permitted_groups',  # Direct instances
            'amenity_ids'
        ]
        read_only_fields = ('id',)
        extra_kwargs = {
            'min_booking_duration': {'allow_null': True},
            'max_booking_duration': {'allow_null': True},
            'buffer_time_minutes': {'allow_null': True},
            'image': {'required': False, 'allow_null': True}
        }

    def validate(self, data):
        """
        序列化器层面的自定义验证。
        """
        instance = self.instance

        # 1. 时间字段一致性验证
        start_time_new = data.get('available_start_time', instance.available_start_time if instance else None)
        end_time_new = data.get('available_end_time', instance.available_end_time if instance else None)

        if start_time_new and end_time_new and start_time_new >= end_time_new:
            raise serializers.ValidationError(
                {'available_end_time': '每日最晚可预订时间必须晚于最早可预订时间。'},
                code='invalid_time_range'
            )

        # 2. is_active, is_bookable, is_container 业务规则验证
        is_active_new = data.get('is_active', instance.is_active if instance else True)
        is_bookable_new = data.get('is_bookable', instance.is_bookable if instance else True)
        is_container_new = data.get('is_container', instance.is_container if instance else False)

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
        space_type_new = data.get('space_type',
                                  instance.space_type if instance else None)  # space_type_new is SpaceType instance
        if space_type_new and not space_type_new.default_is_bookable and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': f"所属空间类型 '{space_type_new.name}' 默认不可预订，此空间不能设置为可预订。"},
                code='space_type_not_bookable_conflict'
            )

        # 4. 父级空间不能是自身
        parent_space_new = data.get('parent_space',
                                    instance.parent_space if instance else None)  # parent_space_new is Space instance
        if parent_space_new and instance and parent_space_new == instance:
            raise serializers.ValidationError(
                {'parent_space': '空间不能将自身设置为父级空间。'},
                code='self_parent_space'
            )

        # 5. 确保 is_bookable_individually 为 False 的 Amenity 不会尝试单独预订
        amenity_ids = data.get('amenity_ids', None)
        if amenity_ids is not None:
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

        # Pop custom fields that are to be handled as separate arguments by the service
        amenity_ids = validated_data.pop('amenity_ids', [])
        permitted_groups = validated_data.pop('permitted_groups', [])  # This contains Group instances

        # Delegating creation and related object handling entirely to SpaceService.
        service_result = SpaceService().create_space(
            user=user,
            space_data=validated_data,  # Contains resolved FK instances like `space_type:<instance>`
            permitted_groups_data=permitted_groups,  # List of Group instances
            amenity_ids_data=amenity_ids  # List of int for Amenity PKs
        )

        if service_result.success:
            # We must return the actual Space model instance, NOT a dict from the service.
            # So, retrieve the newly created model instance from DB by ID.
            return Space.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()

    def update(self, instance, validated_data):
        user = self.context['request'].user

        # Pop custom fields
        amenity_ids = validated_data.pop('amenity_ids', None)
        permitted_groups = validated_data.pop('permitted_groups', None)

        # Delegating update and related object handling entirely to SpaceService.
        service_result = SpaceService().update_space(
            user=user,
            pk=instance.pk,  # instance is the real model instance here
            space_data=validated_data,  # Contains resolved FK instances
            permitted_groups_data=permitted_groups,
            amenity_ids_data=amenity_ids
        )

        if service_result.success:
            # Service.update_space should return a Dict[str, Any] which is a serialized model.
            # So, retrieve the updated model instance from DB by ID.
            return Space.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()


# --------- Amenity Type Serializers ---------

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
            # Service returns dict, load model from DB to satisfy serializer.save() contract
            return Amenity.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()

    def update(self, instance, validated_data):
        user = self.context['request'].user
        service_result = AmenityService().update_amenity(user=user, pk=instance.pk, amenity_data=validated_data)
        if service_result.success:
            # Service returns dict, load model from DB to satisfy serializer.save() contract
            return Amenity.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()


# --------- Space Type Serializers ---------

class SpaceTypeBaseSerializer(serializers.ModelSerializer):
    """
    空间类型（SpaceType）的基础序列化器，包含所有字段。
    """

    class Meta:
        model = SpaceType
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


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
            # Service returns dict, load model from DB to satisfy serializer.save() contract
            return SpaceType.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()

    def update(self, instance, validated_data):
        user = self.context['request'].user
        service_result = SpaceTypeService().update_space_type(user=user, pk=instance.pk, space_type_data=validated_data)
        if service_result.success:
            # Service returns dict, load model from DB to satisfy serializer.save() contract
            return SpaceType.objects.get(pk=service_result.data['id'])
        else:
            raise service_result.to_exception()