# spaces/serializers.py
from rest_framework import serializers

# 从 bookings.api.serializers 导入，避免在 core 层次创建新的文件，保持一致性
from bookings.api.serializers import UserSerializerMinimal

from spaces.models import Amenity, Space, SpaceType, BookableAmenity
from core.utils.constants import MSG_BAD_REQUEST
from users.models import CustomUser
from django.contrib.auth.models import Group


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
    空间基础序列化器，包含所有字段。
    """
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    managed_by = UserSerializerMinimal(read_only=True)
    bookable_amenities = BookableAmenitySerializer(many=True, read_only=True)
    # --- Renamed from restricted_groups_display to permitted_groups_display ---
    permitted_groups_display = serializers.SerializerMethodField()

    # --- END Renamed ---

    class Meta:
        model = Space
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')

    # --- Renamed from get_restricted_groups_display to get_permitted_groups_display ---
    def get_permitted_groups_display(self, obj):
        return ", ".join([group.name for group in obj.permitted_groups.all()]) if obj.permitted_groups.exists() else "无"
    # --- END Renamed ---


class SpaceListSerializer(SpaceBaseSerializer):
    """
    空间列表序列化器。
    """

    class Meta(SpaceBaseSerializer.Meta):
        fields = [
            'id', 'name', 'location', 'capacity', 'is_bookable',
            'requires_approval', 'image', 'bookable_amenities',
            'description', 'space_type', 'permitted_groups_display'  # 更新字段名称
        ]
        read_only_fields = ('id', 'bookable_amenities', 'space_type', 'permitted_groups_display')


class SpaceCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间创建和更新序列化器。
    """
    space_type_id = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), source='space_type', write_only=True, required=False, allow_null=True,
        help_text="空间类型的ID，例如：1"
    )
    parent_space_id = serializers.PrimaryKeyRelatedField(
        queryset=Space.objects.all(), source='parent_space', write_only=True, required=False, allow_null=True,
        help_text="父级空间的ID，例如：2"
    )
    managed_by_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), source='managed_by', write_only=True, required=False, allow_null=True,
        help_text="主要管理人员的ID，例如：3"
    )

    amenity_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        allow_empty=True,
        help_text="以整数列表形式传入设施ID, 例如: [1, 2, 3]"
    )

    # --- Renamed from restricted_groups to permitted_groups ---
    permitted_groups = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), many=True,
        required=False,
        help_text="可预订用户组的ID列表，例如: [1, 2]"
    )

    # --- END Renamed ---

    class Meta:
        model = Space
        fields = [
            'id', 'name', 'location', 'description', 'capacity',
            'is_bookable', 'is_active', 'is_container', 'requires_approval', 'image',
            'available_start_time', 'available_end_time',
            'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes',
            'space_type_id', 'parent_space_id', 'managed_by_id', 'permitted_groups',  # 更新字段名称
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
        space_type_new = data.get('space_type', instance.space_type if instance else None)
        if space_type_new and not space_type_new.default_is_bookable and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': f"所属空间类型 '{space_type_new.name}' 默认不可预订，此空间不能设置为可预订。"},
                code='space_type_not_bookable_conflict'
            )

        # 4. 父级空间不能是自身
        parent_space_new = data.get('parent_space', instance.parent_space if instance else None)
        if parent_space_new and instance and parent_space_new == instance:
            raise serializers.ValidationError(
                {'parent_space_id': '空间不能将自身设置为父级空间。'},
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
        amenity_ids = validated_data.pop('amenity_ids', [])
        # --- Renamed from restricted_groups to permitted_groups ---
        permitted_groups = validated_data.pop('permitted_groups', [])
        # --- END Renamed ---

        instance = super().create(validated_data)
        instance.permitted_groups.set(permitted_groups)  # 更新字段名称
        return instance

    def update(self, instance, validated_data):
        amenity_ids = validated_data.pop('amenity_ids', None)
        # --- Renamed from restricted_groups to permitted_groups ---
        permitted_groups = validated_data.pop('permitted_groups', None)
        # --- END Renamed ---

        instance = super().update(instance, validated_data)

        if permitted_groups is not None:
            instance.permitted_groups.set(permitted_groups)  # 更新字段名称

        return instance


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


# --------- Space Type Serializers ---------

class SpaceTypeBaseSerializer(serializers.ModelSerializer):
    """
    空间类型（SpaceType）的基础序列化器，包含所有字段。
    """

    class Meta:
        model = SpaceType
        fields = '__all__'  # 字段已在模型中移除，此处无需特别处理
        read_only_fields = ('id', 'created_at', 'updated_at')


class SpaceTypeCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间类型创建和更新序列化器。
    """

    class Meta:
        model = SpaceType
        fields = '__all__'  # 字段已在模型中移除，此处无需特别处理
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