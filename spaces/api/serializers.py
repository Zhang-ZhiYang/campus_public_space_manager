# spaces/serializers.py
from rest_framework import serializers

# 从 bookings.api.serializers 导入，避免在 core 层次创建新的文件，保持一致性
# 如果 bookings 模块尚未包含 UserSerializerMinimal，请将其放在 core/utils/serializers.py
# 或在此处重新定义一个简版
# 这里直接从 bookings 导入，确保它已经被定义且可用
from bookings.api.serializers import UserSerializerMinimal

from spaces.models import Amenity, Space, SpaceType, BookableAmenity  # 导入 SpaceType, BookableAmenity
from core.utils.constants import MSG_BAD_REQUEST
from users.models import CustomUser  # 导入 CustomUser
from django.contrib.auth.models import Group  # 导入 Group


# Minimal Serializers for Nested Relationships (如果 bookings.api.serializers 中没有或不希望交叉引用)
class SpaceTypeSerializerMinimal(serializers.ModelSerializer):
    space_type_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = SpaceType
        fields = ('space_type_id', 'name')
        read_only_fields = ('space_type_id', 'name')


# UserSerializerMinimal 假设从 bookings.api.serializers 导入

class AmenitySerializer(serializers.ModelSerializer):
    """
    设施序列化器，用于在Space详情中嵌套显示和设施的CRUD操作。
    """

    class Meta:
        model = Amenity
        fields = ['id', 'name', 'description', 'is_bookable_individually']  # 添加 is_bookable_individually
        read_only_fields = ['id']


class BookableAmenitySerializer(serializers.ModelSerializer):
    """
    可预订设施实例的序列化器，用于 Space 详情页的嵌套显示。
    """
    amenity = AmenitySerializer(read_only=True)  # 嵌套显示 Amenity 详情

    class Meta:
        model = BookableAmenity
        fields = ['id', 'amenity', 'quantity', 'is_bookable', 'is_active']
        read_only_fields = ['id']  # space 外键不需要显示，因为在 Space 内部嵌套


class SpaceBaseSerializer(serializers.ModelSerializer):
    """
    空间基础序列化器，包含所有字段。
    用于管理员权限的详细视图，或者作为其他序列化器的基类。
    amenities 字段是嵌套的只读显示。
    """
    # 嵌套显示关联对象
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    managed_by = UserSerializerMinimal(read_only=True)
    bookable_amenities = BookableAmenitySerializer(many=True, read_only=True)  # 替换原来的 amenities 字段
    restricted_groups_display = serializers.SerializerMethodField()  # 用于显示受限组名称

    class Meta:
        model = Space
        fields = '__all__'  # 包含模型所有字段
        read_only_fields = ('id', 'created_at', 'updated_at')

    def get_restricted_groups_display(self, obj):
        return [group.name for group in obj.restricted_groups.all()]


class SpaceListSerializer(SpaceBaseSerializer):
    """
    空间列表序列化器，用于普通用户和列表展示。
    精简字段，只展示关键信息。
    """

    class Meta(SpaceBaseSerializer.Meta):
        fields = [
            'id', 'name', 'location', 'capacity', 'is_bookable',
            'requires_approval', 'image', 'bookable_amenities',  # 替换 amenities
            'description', 'space_type', 'restricted_groups_display'  # 列表也显示 space_type 和受限组
        ]
        read_only_fields = ('id', 'bookable_amenities', 'space_type',
                            'restricted_groups_display')  # bookable_amenities read-only for list view


class SpaceCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间创建和更新序列化器。
    amenity_ids 字段用于接收设施ID列表（写入时），而非嵌套对象。
    space_type_id 和 managed_by_id 用于写入时指定关联对象。
    """
    # Write-only fields for related object IDs during create/update
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
        child=serializers.IntegerField(),  # 直接接收整数ID
        write_only=True,
        required=False,
        allow_empty=True,
        help_text="以整数列表形式传入设施ID, 例如: [1, 2, 3]"
    )

    # restricted_groups 字段用于接收 Group ID 列表 (ManyToMany)
    restricted_groups = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), many=True,
        required=False,  # 可以不传
        help_text="受限用户组的ID列表，例如: [1, 2]"
    )

    class Meta:
        model = Space
        fields = [
            'id', 'name', 'location', 'description', 'capacity',
            'is_bookable', 'is_active', 'is_container', 'requires_approval', 'image',
            'available_start_time', 'available_end_time',
            'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes',
            'space_type_id', 'parent_space_id', 'managed_by_id', 'restricted_groups',  # 新增关联字段
            'amenity_ids'
        ]
        read_only_fields = ('id',)  # ID 是只读的，不可更新
        extra_kwargs = {
            'min_booking_duration': {'allow_null': True},  # 允许为空
            'max_booking_duration': {'allow_null': True},  # 允许为空
            'buffer_time_minutes': {'allow_null': True},  # 允许为空
            # 对于 SpaceModel, image 字段，如果不是必填，需要确保可以为 null
            'image': {'required': False, 'allow_null': True}
        }

    def validate(self, data):
        """
        序列化器层面的自定义验证。
        - 验证时间段的有效性。
        - 验证 is_active 和 is_bookable 的一致性。
        - 验证 is_container 和 is_bookable 的一致性。
        - 验证 parent_space 不为自身。
        """
        instance = self.instance  # 获取当前实例（如果存在，用于更新）或 None（用于创建）

        # 1. 时间字段一致性验证
        start_time_new = data.get('available_start_time', instance.available_start_time if instance else None)
        end_time_new = data.get('available_end_time', instance.available_end_time if instance else None)

        if start_time_new and end_time_new and start_time_new >= end_time_new:
            raise serializers.ValidationError(
                {'available_end_time': '每日最晚可预订时间必须晚于最早可预订时间。'},
                code='invalid_time_range'
            )

        # 2. is_active 和 is_bookable 业务规则验证
        is_active_new = data.get('is_active', instance.is_active if instance else True)
        is_bookable_new = data.get('is_bookable', instance.is_bookable if instance else True)
        is_container_new = data.get('is_container', instance.is_container if instance else False)

        if not is_active_new and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': '不活跃的空间不能设置为可预订。'},
                code='inactive_space_not_bookable'
            )

        # 3. is_container 和 is_bookable 业务规则验证
        if is_container_new and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': '容器空间通常不直接预订，请设置 is_bookable 为 False。'},
                code='container_space_not_bookable'
            )

        # 4. 父级空间不能是自身 (Django Model 的 clean() 也会校验，这里提前校验)
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
        # 处理 amenity_ids 和 restricted_groups，这两个字段在 super() 的 create 中会报错
        amenity_ids = validated_data.pop('amenity_ids', [])
        restricted_groups = validated_data.pop('restricted_groups', [])

        instance = super().create(validated_data)
        instance.restricted_groups.set(restricted_groups)  # 设置多对多关系
        # amenity_ids 的处理放在 Service 层，通过 _update_space_amenities
        return instance

    def update(self, instance, validated_data):
        # 处理 amenity_ids 和 restricted_groups
        amenity_ids = validated_data.pop('amenity_ids', None)  # None 表示不更新
        restricted_groups = validated_data.pop('restricted_groups', None)

        instance = super().update(instance, validated_data)

        if restricted_groups is not None:
            instance.restricted_groups.set(restricted_groups)  # 更新多对多关系

        # amenity_ids 的处理放在 Service 层
        return instance


# --------- Amenity Type Serializers ---------

class AmenityBaseSerializer(serializers.ModelSerializer):
    """
    设施类型（Amenity）的基础序列化器，用于在Space详情中嵌套显示和设施的CRUD操作。
    """

    class Meta:
        model = Amenity
        fields = '__all__'
        # Assuming created_at/updated_at fields in Amenity model
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
        # 简化验证，如果未来有更复杂的业务逻辑，可以在这里添加
        return data


# --------- Space Type Serializers ---------

class SpaceTypeBaseSerializer(serializers.ModelSerializer):
    """
    空间类型（SpaceType）的基础序列化器，包含所有字段。
    """

    class Meta:
        model = SpaceType
        fields = '__all__'
        # Assuming created_at/updated_at in SpaceType model
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