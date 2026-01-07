# bookings/api/serializers.py
from rest_framework import serializers
from bookings.models import (
    Booking, Violation, UserPenaltyPointsPerSpaceType, SpaceTypeBanPolicy,
    UserSpaceTypeBan, UserSpaceTypeExemption, BOOKING_STATUS_CHOICES, VIOLATION_TYPE_CHOICES
)
# 确保从正确的地方导入 Amenity, Space, BookableAmenity, SpaceType
from spaces.models import Space, BookableAmenity, Amenity, SpaceType
# 确保从正确的地方导入 CustomUser
from users.models import CustomUser


# --- Minimal Serializers for Nested Relationships ---
# 用于在其他序列化器中嵌套显示关联对象的简要信息

class UserSerializerMinimal(serializers.ModelSerializer):
    # FIX: id -> user_id
    user_id = serializers.IntegerField(source='id', read_only=True)
    full_name = serializers.CharField(source='get_full_name', read_only=True)

    class Meta:
        model = CustomUser
        fields = ('user_id', 'username', 'full_name')  # FIX: 替换 'id'
        read_only_fields = ('user_id', 'username', 'full_name')  # 确保新的只读字段


class SpaceTypeSerializerMinimal(serializers.ModelSerializer):
    # FIX: id -> space_type_id
    space_type_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = SpaceType
        fields = ('space_type_id', 'name')  # FIX: 替换 'id'
        read_only_fields = ('space_type_id', 'name')


class AmenitySerializerMinimal(serializers.ModelSerializer):
    # FIX: id -> amenity_id
    amenity_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = Amenity
        fields = ('amenity_id', 'name')  # FIX: 替换 'id'
        read_only_fields = ('amenity_id', 'name')


class SpaceSerializerMinimal(serializers.ModelSerializer):
    # FIX: id -> space_id
    space_id = serializers.IntegerField(source='id', read_only=True)
    space_type = SpaceTypeSerializerMinimal(read_only=True)

    class Meta:
        model = Space
        fields = ('space_id', 'name', 'description', 'capacity', 'is_bookable', 'is_active',
                  'space_type')  # FIX: 替换 'id'
        read_only_fields = ('space_id', 'name', 'description', 'capacity', 'is_bookable', 'is_active', 'space_type')


class BookableAmenitySerializerMinimal(serializers.ModelSerializer):
    # FIX: id -> bookable_amenity_id
    bookable_amenity_id = serializers.IntegerField(source='id', read_only=True)
    amenity = AmenitySerializerMinimal(read_only=True)
    space = SpaceSerializerMinimal(read_only=True)

    class Meta:
        model = BookableAmenity
        fields = ('bookable_amenity_id', 'quantity', 'is_active', 'is_bookable', 'amenity', 'space')  # FIX: 替换 'id'
        read_only_fields = ('bookable_amenity_id', 'quantity', 'is_active', 'is_bookable', 'amenity', 'space')


# --- Booking Serializers ---

class BookingShortSerializer(serializers.ModelSerializer):
    """
    预订的简要视图，用于列表和嵌套显示。
    """
    # 保持不变，已是 booking_id
    booking_id = serializers.IntegerField(source='id', read_only=True)
    user = UserSerializerMinimal(read_only=True)
    space = SpaceSerializerMinimal(read_only=True)
    bookable_amenity = BookableAmenitySerializerMinimal(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Booking
        fields = (
            'booking_id',
            'user', 'space', 'bookable_amenity', 'booked_quantity',
            'start_time', 'end_time', 'purpose', 'status', 'status_display',
            'created_at'
        )
        read_only_fields = ('booking_id', 'user', 'status', 'status_display', 'created_at')


class BookingCreateSerializer(serializers.ModelSerializer):
    """
    用于创建预订的序列化器。
    要求传入 space_id 或 bookable_amenity_id。
    """
    # 这里不需要改变，因为是写入时传入的 id
    space_id = serializers.PrimaryKeyRelatedField(
        queryset=Space.objects.all(), source='space', write_only=True, required=False, allow_null=True
    )
    bookable_amenity_id = serializers.PrimaryKeyRelatedField(
        queryset=BookableAmenity.objects.all(), source='bookable_amenity', write_only=True, required=False,
        allow_null=True
    )

    class Meta:
        model = Booking
        fields = (
            'space_id', 'bookable_amenity_id', 'start_time', 'end_time',
            'purpose', 'booked_quantity'
        )
        extra_kwargs = {
            'start_time': {'required': True},
            'end_time': {'required': True},
            'purpose': {'required': True, 'allow_blank': False},
            'booked_quantity': {'required': False, 'allow_null': True, 'default': 1}
        }

    def validate(self, data):
        space = data.get('space')
        bookable_amenity = data.get('bookable_amenity')

        if not space and not bookable_amenity:
            raise serializers.ValidationError("预订必须指定一个空间ID或设施ID。")
        if space and bookable_amenity:
            raise serializers.ValidationError("预订不能同时指定空间和可预订设施。")

        booked_quantity = data.get('booked_quantity')
        if booked_quantity is None:
            booked_quantity = 1
            data['booked_quantity'] = booked_quantity

        if booked_quantity <= 0:
            raise serializers.ValidationError({'booked_quantity': '预订数量必须大于0。'})

        if space and booked_quantity != 1:
            raise serializers.ValidationError({'booked_quantity': '预订整个空间时，数量必须为1。'})

        return data


class BookingDetailSerializer(BookingShortSerializer):
    """
    预订的详细视图，包含管理员相关信息。
    """
    reviewed_by = UserSerializerMinimal(read_only=True)

    class Meta(BookingShortSerializer.Meta):
        fields = BookingShortSerializer.Meta.fields + (
            'admin_notes', 'reviewed_by', 'reviewed_at', 'updated_at'
        )
        # 继承所有 read_only_fields


class BookingStatusUpdateSerializer(serializers.Serializer):
    """
    用于更新预订状态的序列化器。
    """
    status = serializers.ChoiceField(
        choices=[(k, v) for k, v in BOOKING_STATUS_CHOICES if k not in ['PENDING', 'CANCELLED']],
        help_text="新的预订状态 (APPROVED, REJECTED, CHECKED_IN, CHECKED_OUT, COMPLETED, NO_SHOW)"
    )
    admin_notes = serializers.CharField(
        required=False, allow_blank=True, max_length=500,
        help_text="管理员的备注信息"
    )

    def validate_status(self, value):
        if value not in ['APPROVED', 'REJECTED', 'CHECKED_IN', 'CHECKED_OUT', 'COMPLETED', 'NO_SHOW']:
            raise serializers.ValidationError("不支持的状态类型。")
        return value


# --- Violation Serializers ---

class ViolationSerializer(serializers.ModelSerializer):
    # FIX: id -> violation_id
    violation_id = serializers.IntegerField(source='id', read_only=True)
    user = UserSerializerMinimal(read_only=True)
    booking = BookingShortSerializer(read_only=True)  # 嵌套的 BookingShortSerializer 会输出 booking_id
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    issued_by = UserSerializerMinimal(read_only=True)
    resolved_by = UserSerializerMinimal(read_only=True)
    violation_type_display = serializers.CharField(source='get_violation_type_display', read_only=True)

    class Meta:
        model = Violation
        # FIX: 将 '__all__' 替换为显式列出的字段，并替换 'id'
        fields = (
            'violation_id', 'user', 'booking', 'space_type', 'violation_type',
            'description', 'penalty_points', 'issued_by', 'issued_at',
            'is_resolved', 'resolved_at', 'resolved_by', 'violation_type_display'
        )
        read_only_fields = (
            'violation_id', 'user', 'booking', 'space_type', 'issued_by', 'issued_at',
            'resolved_by', 'resolved_at', 'violation_type_display', 'is_resolved'
        )


# --- Penalty Points Serializers ---

class UserPenaltyPointsPerSpaceTypeSerializer(serializers.ModelSerializer):
    # FIX: id -> penalty_points_record_id
    penalty_points_record_id = serializers.IntegerField(source='id', read_only=True)
    user = UserSerializerMinimal(read_only=True)
    space_type = SpaceTypeSerializerMinimal(read_only=True)

    class Meta:
        model = UserPenaltyPointsPerSpaceType
        # FIX: 将 '__all__' 替换为显式列出的字段，并替换 'id'
        fields = (
            'penalty_points_record_id', 'user', 'space_type', 'current_penalty_points',
            'last_violation_at', 'last_ban_trigger_at', 'updated_at'
        )
        read_only_fields = (
            'penalty_points_record_id', 'user', 'space_type', 'current_penalty_points',
            'last_violation_at', 'last_ban_trigger_at', 'updated_at'
        )


# --- Ban Policy Serializers ---

class SpaceTypeBanPolicySerializer(serializers.ModelSerializer):
    # FIX: id -> ban_policy_id
    ban_policy_id = serializers.IntegerField(source='id', read_only=True)
    space_type = SpaceTypeSerializerMinimal(read_only=True)

    class Meta:
        model = SpaceTypeBanPolicy
        # FIX: 将 '__all__' 替换为显式列出的字段，并替换 'id'
        fields = (
            'ban_policy_id', 'space_type', 'threshold_points', 'ban_duration',
            'priority', 'is_active', 'description', 'created_at', 'updated_at'
        )
        read_only_fields = (
            'ban_policy_id', 'created_at', 'updated_at'
        )


# --- User Ban Serializers ---

class UserSpaceTypeBanSerializer(serializers.ModelSerializer):
    # FIX: id -> user_ban_id
    user_ban_id = serializers.IntegerField(source='id', read_only=True)
    user = UserSerializerMinimal(read_only=True)
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    ban_policy_applied = SpaceTypeBanPolicySerializer(read_only=True)
    issued_by = UserSerializerMinimal(read_only=True)

    class Meta:
        model = UserSpaceTypeBan
        # FIX: 将 '__all__' 替换为显式列出的字段，并替换 'id'
        fields = (
            'user_ban_id', 'user', 'space_type', 'start_date', 'end_date',
            'ban_policy_applied', 'reason', 'issued_by', 'issued_at'
        )
        read_only_fields = (
            'user_ban_id', 'user', 'space_type', 'ban_policy_applied', 'start_date', 'end_date', 'issued_by',
            'issued_at'
        )


# --- Exemption Serializers ---

class UserSpaceTypeExemptionSerializer(serializers.ModelSerializer):
    # FIX: id -> exemption_id
    exemption_id = serializers.IntegerField(source='id', read_only=True)
    user = UserSerializerMinimal(read_only=True)
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    granted_by = UserSerializerMinimal(read_only=True)

    class Meta:
        model = UserSpaceTypeExemption
        # FIX: 将 '__all__' 替换为显式列出的字段，并替换 'id'
        fields = (
            'exemption_id', 'user', 'space_type', 'exemption_reason',
            'start_date', 'end_date', 'granted_by', 'granted_at'
        )
        read_only_fields = (
            'exemption_id', 'user', 'space_type', 'granted_by', 'granted_at'
        )