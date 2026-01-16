# bookings/api/serializers.py
from rest_framework import serializers
import uuid
import datetime
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from bookings.models import (
    Booking, Violation, UserPenaltyPointsPerSpaceType, SpaceTypeBanPolicy,
    UserSpaceTypeBan, UserSpaceTypeExemption, DailyBookingLimit
)
from spaces.models import Space, BookableAmenity, SpaceType

# --- IMPORTANT: Now importing from dedicated app serializers to avoid circular dependencies ---
# Assuming these files exist in their respective apps' api/serializers.py
from users.api.serializers import UserSerializerMinimal
from spaces.api.serializers import (
    SpaceTypeSerializerMinimal,
    AmenitySerializer, # Using the full AmenitySerializer for nesting
    BookableAmenitySerializerMinimal,
    SpaceSerializerMinimal,
)
# --- End of import changes ---

CustomUser = get_user_model()

# --- Common/Minimal Serializers that belong to bookings or are genuinely generic (Group) ---
class GroupSerializerMinimal(serializers.ModelSerializer):
    group_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = Group
        fields = ('group_id', 'name')

# --- Booking Serializers ---
class BookingCreateSerializer(serializers.Serializer):
    """用于创建新预订的序列化器。"""
    space_id = serializers.IntegerField(required=False, allow_null=True, help_text="预订空间的ID")
    bookable_amenity_id = serializers.IntegerField(required=False, allow_null=True, help_text="预订设施的ID")
    start_time = serializers.DateTimeField(help_text="预订开始时间 (ISO 8601格式，例如: 2023-10-27T10:00:00Z)")
    end_time = serializers.DateTimeField(help_text="预订结束时间 (ISO 8601格式，例如: 2023-10-27T12:00:00Z)")
    booked_quantity = serializers.IntegerField(min_value=1, default=1, help_text="预订数量，预订整个空间时应为1")
    purpose = serializers.CharField(max_length=500, required=False, allow_blank=True, help_text="预订用途")
    request_uuid = serializers.UUIDField(default=uuid.uuid4, format='hex_verbose',
                                         help_text="客户端生成的请求唯一标识，用于幂等性")
    expected_attendees = serializers.IntegerField(min_value=1, required=False, allow_null=True,
                                                  help_text="预期参与人数，预订整个空间时必填且大于0")

    def validate(self, data):
        space_id = data.get('space_id')
        bookable_amenity_id = data.get('bookable_amenity_id')

        if not (space_id or bookable_amenity_id):
            raise serializers.ValidationError(
                {"target_id": "必须指定预订空间 (space_id) 或可预订设施 (bookable_amenity_id) 之一。"},
                code="missing_target")
        if space_id and bookable_amenity_id:
            raise serializers.ValidationError("不能同时指定 space_id 和 bookable_amenity_id。",
                                              code="mutually_exclusive_target")

        if data['start_time'] >= data['end_time']:
            raise serializers.ValidationError({"end_time": "结束时间必须晚于开始时间。"}, code="invalid_time_order")

        # 兼容 Service 层的时间提前量，让这里稍微宽松一些
        if data['start_time'] < timezone.now() - datetime.timedelta(minutes=5):
            raise serializers.ValidationError({"start_time": "不能预订过去的时间，或时间已接近当前，请选择未来时间。"},
                                              code="past_booking_not_allowed")

        if space_id and not bookable_amenity_id:
            expected_attendees = data.get('expected_attendees')
            if expected_attendees is None or expected_attendees <= 0:
                raise serializers.ValidationError({"expected_attendees": "预订整个空间时，必须提供大于0的预期参与人数。"},
                                                  code="expected_attendees_required")
        elif bookable_amenity_id and data.get('expected_attendees') is not None and data.get('expected_attendees') <= 0:
            raise serializers.ValidationError({"expected_attendees": "预期参与人数必须大于0。"},
                                              code="invalid_expected_attendees")

        return data

class BookingSerializer(serializers.ModelSerializer):
    """预订详情和列表的序列化器。"""
    user = UserSerializerMinimal(read_only=True)
    space = SpaceSerializerMinimal(read_only=True)
    bookable_amenity = BookableAmenitySerializerMinimal(read_only=True)
    related_space = SpaceSerializerMinimal(read_only=True)  # 冗余字段但方便
    reviewed_by = UserSerializerMinimal(read_only=True)

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    processing_status_display = serializers.CharField(source='get_processing_status_display', read_only=True)

    class Meta:
        model = Booking
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at', 'request_uuid', 'user', 'space', 'bookable_amenity',
                            'related_space', 'reviewed_by', 'status_display', 'processing_status_display')

class BookingUpdateSerializer(serializers.ModelSerializer):
    """用于更新预订某些字段的序列化器，例如 purpose 或管理员备注。"""
    purpose = serializers.CharField(max_length=500, required=False, allow_blank=True)
    admin_notes = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Booking.BOOKING_STATUS_CHOICES, required=False)  # 管理员可更改

    class Meta:
        model = Booking
        fields = ('purpose', 'admin_notes', 'status')

    def validate_status(self, value):
        if self.instance and self.instance.status != value:
            user = self.context['request'].user
            if not user.is_superuser:
                if 'status' in self.initial_data and value != self.instance.status:
                    if value == 'CANCELLED':
                        if self.instance.status not in ['PENDING', 'APPROVED']:
                            raise serializers.ValidationError("只有待审核或已批准的预订才能被取消。",
                                                              code="invalid_cancel_status")
                    else:
                        raise serializers.ValidationError("您没有权限进行除取消外的其他状态变更。",
                                                          code="permission_denied_status_change")
        return value

# --- Violation Serializers ---
class ViolationSerializer(serializers.ModelSerializer):
    user = UserSerializerMinimal(read_only=True)
    booking = BookingSerializer(read_only=True) # 可以用BookingSerializerMinimal
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    issued_by = UserSerializerMinimal(read_only=True)
    resolved_by = UserSerializerMinimal(read_only=True)

    violation_type_display = serializers.CharField(source='get_violation_type_display', read_only=True)

    class Meta:
        model = Violation
        fields = '__all__'
        read_only_fields = ('id', 'issued_at', 'user', 'booking', 'space_type', 'issued_by', 'resolved_by',
                            'violation_type_display')

class ViolationCreateUpdateSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), help_text="违约用户ID")
    booking = serializers.PrimaryKeyRelatedField(
        queryset=Booking.objects.all(), required=False, allow_null=True, help_text="关联预订ID")
    space_type = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), required=False, allow_null=True, help_text="违约所属空间类型ID")
    issued_by = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), required=False, allow_null=True, help_text="记录人员ID (默认为当前操作者)")

    class Meta:
        model = Violation
        fields = '__all__'
        read_only_fields = ('id', 'issued_at')

# --- Penalty (UserPenaltyPointsPerSpaceType) Serializers ---
class UserPenaltyPointsSerializer(serializers.ModelSerializer):
    user = UserSerializerMinimal(read_only=True)
    space_type = SpaceTypeSerializerMinimal(read_only=True)

    class Meta:
        model = UserPenaltyPointsPerSpaceType
        fields = '__all__'
        read_only_fields = ('id', 'user', 'space_type', 'current_penalty_points', 'last_violation_at',
                            'last_ban_trigger_at', 'updated_at')

# --- BanPolicy Serializers ---
class BanPolicySerializer(serializers.ModelSerializer):
    space_type = SpaceTypeSerializerMinimal(read_only=True)

    ban_duration_display = serializers.SerializerMethodField()

    class Meta:
        model = SpaceTypeBanPolicy
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')

    def get_ban_duration_display(self, obj) -> str:
        if obj.ban_duration:
            total_seconds = int(obj.ban_duration.total_seconds())
            days = total_seconds // (24 * 3600)
            hours = (total_seconds % (24 * 3600)) // 3600
            minutes = (total_seconds % 3600) // 60

            parts = []
            if days > 0: parts.append(f"{days}天")
            if hours > 0: parts.append(f"{hours}小时")
            if minutes > 0: parts.append(f"{minutes}分钟")

            return " ".join(parts) if parts else "0分钟"
        return "N/A"

class BanPolicyCreateUpdateSerializer(serializers.ModelSerializer):
    space_type = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), required=False, allow_null=True, help_text="策略应用空间类型ID")

    class Meta:
        model = SpaceTypeBanPolicy
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')

# --- UserBan Serializers ---
class UserBanSerializer(serializers.ModelSerializer):
    user = UserSerializerMinimal(read_only=True)
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    # Nests BanPolicySerializer which is defined above in this same file
    ban_policy_applied = BanPolicySerializer(read_only=True)
    issued_by = UserSerializerMinimal(read_only=True)

    is_active = serializers.SerializerMethodField()

    class Meta:
        model = UserSpaceTypeBan
        fields = '__all__'
        read_only_fields = ('id', 'issued_at', 'user', 'space_type', 'ban_policy_applied', 'issued_by')

    def get_is_active(self, obj) -> bool:
        return obj.end_date > timezone.now()

class UserBanCreateUpdateSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), help_text="被禁用用户ID")
    space_type = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), required=False, allow_null=True, help_text="禁用空间类型ID")
    ban_policy_applied = serializers.PrimaryKeyRelatedField(
        queryset=SpaceTypeBanPolicy.objects.all(), required=False, allow_null=True, help_text="应用禁用策略ID")
    issued_by = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), required=False, allow_null=True, help_text="执行禁用人员ID")

    class Meta:
        model = UserSpaceTypeBan
        fields = '__all__'
        read_only_fields = ('id', 'issued_at')

# --- UserExemption Serializers ---
class UserExemptionSerializer(serializers.ModelSerializer):
    user = UserSerializerMinimal(read_only=True)
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    granted_by = UserSerializerMinimal(read_only=True)

    is_active = serializers.SerializerMethodField()

    class Meta:
        model = UserSpaceTypeExemption
        fields = '__all__'
        read_only_fields = ('id', 'granted_at', 'user', 'space_type', 'granted_by')

    def get_is_active(self, obj) -> bool:
        current_time = timezone.now()
        start_check = obj.start_date is None or obj.start_date <= current_time
        end_check = obj.end_date is None or obj.end_date > current_time
        return start_check and end_check

class UserExemptionCreateUpdateSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), help_text="豁免用户ID")
    space_type = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), required=False, allow_null=True, help_text="豁免空间类型ID")
    granted_by = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), required=False, allow_null=True, help_text="授权人员ID")

    class Meta:
        model = UserSpaceTypeExemption
        fields = '__all__'
        read_only_fields = ('id', 'granted_at')

# --- DailyBookingLimit Serializers ---
class DailyBookingLimitSerializer(serializers.ModelSerializer):
    group = GroupSerializerMinimal(read_only=True)
    space_type = SpaceTypeSerializerMinimal(read_only=True)

    class Meta:
        model = DailyBookingLimit
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')

class DailyBookingLimitCreateUpdateSerializer(serializers.ModelSerializer):
    group = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), help_text="用户组ID")
    space_type = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), required=False, allow_null=True, help_text="限制应用空间类型ID")

    class Meta:
        model = DailyBookingLimit
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')