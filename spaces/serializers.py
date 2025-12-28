# spaces/serializers.py
from rest_framework import serializers
from spaces.models import Amenity, Space
from core.utils.constants import MSG_BAD_REQUEST


class AmenitySerializer(serializers.ModelSerializer):
    """
    设施序列化器，用于在Space详情中嵌套显示和设施的CRUD操作。
    """

    class Meta:
        model = Amenity
        fields = ['id', 'name', 'description']
        read_only_fields = ['id']


class AmenityIDSerializer(serializers.PrimaryKeyRelatedField):
    """
    用于处理设施ID列表的字段，确保ID存在。
    此字段在创建/更新 Space 时，用于接收 Amenity 的 ID。
    """

    def get_queryset(self):
        return Amenity.objects.all()

    def to_internal_value(self, data):
        try:
            return super().to_internal_value(data)
        except Amenity.DoesNotExist:
            raise serializers.ValidationError(f"设施ID {data} 未找到。")
        except TypeError:
            raise serializers.ValidationError(f"设施ID必须是整数。")


class SpaceBaseSerializer(serializers.ModelSerializer):
    """
    空间基础序列化器，包含所有字段。
    用于管理员权限的详细视图，或者作为其他序列化器的基类。
    amenities 字段是嵌套的只读显示。
    """
    amenities = AmenitySerializer(many=True, read_only=True)

    class Meta:
        model = Space
        fields = '__all__'  # 包含模型所有字段
        read_only_fields = ('id', 'created_at', 'updated_at')


class SpaceListSerializer(SpaceBaseSerializer):
    """
    空间列表序列化器，用于普通用户和列表展示。
    精简字段，只展示关键信息。
    """

    class Meta(SpaceBaseSerializer.Meta):
        fields = [
            'id', 'name', 'location', 'capacity', 'is_bookable',
            'requires_approval', 'image', 'amenities', 'description'
        ]
        read_only_fields = ('id', 'amenities')  # amenities read-only for list view


class SpaceCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间创建和更新序列化器。
    amenity_ids 字段用于接收设施ID列表（写入时），而非嵌套对象。
    """
    # Write-only field for amenity IDs during create/update
    amenity_ids = serializers.ListField(
        child=serializers.IntegerField(),  # 直接接收整数ID
        write_only=True,
        required=False,
        allow_empty=True,
        help_text="以整数列表形式传入设施ID, 例如: [1, 2, 3]"
    )

    class Meta:
        model = Space
        fields = [
            'id', 'name', 'location', 'description', 'capacity',
            'is_bookable', 'is_active', 'requires_approval', 'image',
            'available_start_time', 'available_end_time',
            'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes',
            'amenity_ids'  # 这个字段用于写入 amenities
        ]
        read_only_fields = ('id',)  # ID 是只读的，不可更新

    def validate(self, data):
        """
        序列化器层面的自定义验证。
        - 验证时间段的有效性。
        - 验证 is_active 和 is_bookable 的一致性。
        """
        # 获取当前实例（如果存在，用于更新）或默认值（用于创建）
        instance = self.instance

        # --- 时间字段一致性验证 ---
        # 对于 partial_update (PATCH), fields might be missing, so use existing instance values if not provided
        start_time_new = data.get('available_start_time', instance.available_start_time if instance else None)
        end_time_new = data.get('available_end_time', instance.available_end_time if instance else None)

        if start_time_new and end_time_new and start_time_new >= end_time_new:
            raise serializers.ValidationError(
                {'available_end_time': '每日最晚可预订时间必须晚于最早可预订时间。'},
                code='invalid_time_range'
            )

        # --- is_active 和 is_bookable 业务规则验证 ---
        is_active_new = data.get('is_active', instance.is_active if instance else True)
        is_bookable_new = data.get('is_bookable', instance.is_bookable if instance else True)

        # 重点：如果 is_active 被设置为 False，那么 is_bookable 不能是 True
        if not is_active_new and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': '不活跃的空间不能设置为可预订。'},
                code='inactive_space_not_bookable'
            )

        return data
