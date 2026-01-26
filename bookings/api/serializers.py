# bookings/api/serializers.py

import uuid
import logging
from datetime import datetime
from typing import Dict, Any, Union, Optional

from django.contrib.auth.models import Group
from rest_framework import serializers
from django.utils import timezone

# 导入 Booking 和 Violation 模型
# 同时导入全局定义的 choices 元组
from bookings.models import (
    Booking as BookingModel,  # 使用别名以符合你的导入习惯
    Violation,  # 需要 Violation 类来声明 Meta.model
    BOOKING_STATUS_CHOICES_TUPLE,  # 从全局导入状态choices元组
    PROCESSING_STATUS_CHOICES_TUPLE,  # 从全局导入处理状态choices元组
    VIOLATION_TYPE_CHOICES, UserPenaltyPointsPerSpaceType, UserSpaceTypeExemption,
    UserSpaceTypeBan, SpaceTypeBanPolicy, DailyBookingLimit  # 从全局导入违规类型choices元组
)

from bookings.service.booking_preliminary_service import BookingPreliminaryService
from core.utils.exceptions import CustomAPIException, InternalServerError, ForbiddenException, BadRequestException
from core.utils.constants import HTTP_200_OK, HTTP_202_ACCEPTED
from spaces.models import BookableAmenity, CustomUser, SpaceType, Space

logger = logging.getLogger(__name__)


# --- Helper Serializers for nested representation (这些是针对实际模型实例的) ---
class CustomUserMinimalSerializer(serializers.ModelSerializer):
    """用于序列化 CustomUser 模型的最小信息"""

    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'get_full_name']
        read_only_fields = ['username', 'email', 'get_full_name']


class SpaceMinimalSerializer(serializers.ModelSerializer):
    """用于序列化 Space 模型的最小信息"""

    class Meta:
        model = Space
        fields = ['id', 'name', 'location', 'capacity', 'is_bookable', 'is_active']  # 添加 is_active
        read_only_fields = ['id', 'name', 'location', 'capacity', 'is_bookable', 'is_active']


class BookableAmenityMinimalSerializer(serializers.ModelSerializer):
    """用于序列化 BookableAmenity 模型的最小信息，包含关联的 Amenity 名称和 Space 名称"""
    amenity_name = serializers.CharField(source='amenity.name', read_only=True)
    space_name = serializers.CharField(source='space.name', read_only=True)

    class Meta:
        model = BookableAmenity
        fields = ['id', 'amenity_name', 'space_name', 'quantity', 'is_bookable', 'is_active']
        read_only_fields = ['id', 'amenity_name', 'space_name', 'quantity', 'is_bookable', 'is_active']


class SpaceTypeMinimalSerializer(serializers.ModelSerializer):
    """用于序列化 SpaceType 模型的最小信息"""

    class Meta:
        model = SpaceType
        fields = ['id', 'name', 'code', 'is_bookable_default']
        read_only_fields = ['id', 'name', 'code', 'is_bookable_default']


class CustomBookingDateTimeField(serializers.DateTimeField):
    """
    一个自定义的 DateTimeField，用于更严格控制预订时间的输入格式和时区处理。
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('input_formats', [
            '%Y-%m-%dT%H:%M:%S%z',  # E.g., 2023-10-27T10:00:00+08:00
            '%Y-%m-%dT%H:%M:%S%Z',  # E.g., 2023-10-27T10:00:00+0800
            '%Y-%m-%dT%H:%M:%SZ',  # E.g., 2023-10-27T10:00:00Z
            '%Y-%m-%d %H:%M:%S%z',
            '%Y-%m-%d %H:%M:%S%Z',
            '%Y-%m-%d %H:%M:%SZ',
            'iso-8601',
        ])
        kwargs.setdefault('format', '%Y-%m-%dT%H:%M:%S%z')
        kwargs.setdefault('default_timezone', timezone.get_current_timezone())
        super().__init__(*args, **kwargs)

    def to_internal_value(self, data):
        datetime_obj = super().to_internal_value(data)
        if timezone.is_aware(datetime_obj):
            return datetime_obj
        return timezone.make_aware(datetime_obj, timezone.get_current_timezone())

    def to_representation(self, value):
        if timezone.is_aware(value):
            return value.astimezone(timezone.get_current_timezone()).strftime(self.format)
        aware_value = timezone.make_aware(value, timezone.get_current_timezone())
        return aware_value.astimezone(timezone.get_current_timezone()).strftime(self.format)


class BookingCreateSerializer(serializers.Serializer):
    """
    创建预订的序列化器。
    """
    space_id = serializers.IntegerField(required=False, allow_null=True,
                                        help_text="预订空间ID (与bookable_amenity_id互斥)")
    bookable_amenity_id = serializers.IntegerField(required=False, allow_null=True,
                                                   help_text="预订可预订设施ID (与space_id互斥)")

    start_time = CustomBookingDateTimeField(
        help_text="预订开始时间 (ISO 8601格式，例如: '2023-10-27T10:00:00+08:00' 或 '2023-10-27T10:00:00Z')")
    end_time = CustomBookingDateTimeField(
        help_text="预订结束时间 (ISO 8601格式，例如: '2023-10-27T11:00:00+08:00' 或 '2023-10-27T11:00:00Z')")

    purpose = serializers.CharField(max_length=255, help_text="预订目的")
    expected_attendees = serializers.IntegerField(min_value=1, required=False, default=1, help_text="预计参与人数")
    booked_quantity = serializers.IntegerField(min_value=1, required=False, default=1,
                                               help_text="预订数量 (例如，几个椅子或会议室可容纳的人数)")
    request_uuid = serializers.UUIDField(format='hex_verbose', required=False, allow_null=True,
                                         help_text="幂等性请求ID (UUID)")

    class Meta:
        pass

    def validate(self, data):
        if data['start_time'] >= data['end_time']:
            raise serializers.ValidationError({'end_time': '结束时间必须晚于开始时间。'})

        space_id = data.get('space_id')
        bookable_amenity_id = data.get('bookable_amenity_id')

        if not space_id and not bookable_amenity_id:
            raise serializers.ValidationError('必须提供 space_id 或 bookable_amenity_id。')
        if space_id and bookable_amenity_id:
            raise serializers.ValidationError('不能同时预订空间和可预订设施。')

        if not data.get('request_uuid'):
            data['request_uuid'] = uuid.uuid4()

        booking_preliminary_service = BookingPreliminaryService()
        user = self.context['request'].user

        pre_validate_data = {
            'space_id': space_id,
            'bookable_amenity_id': bookable_amenity_id,
            'start_time': data['start_time'].isoformat(),
            'end_time': data['end_time'].isoformat(),
            'purpose': data['purpose'],
            'expected_attendees': data['expected_attendees'],
            'booked_quantity': data['booked_quantity'],
            'request_uuid': str(data['request_uuid'])
        }

        try:
            result = booking_preliminary_service.pre_validate(user, pre_validate_data)

            if result.status_code == HTTP_200_OK:
                self.context['idempotent_result'] = result

        except CustomAPIException as e:
            if isinstance(e.detail, dict):
                raise serializers.ValidationError(e.detail, code=getattr(e, 'code', 'custom_api_error'))
            else:
                raise serializers.ValidationError({'non_field_errors': [str(e.detail)]},
                                                  code=getattr(e, 'code', 'custom_api_error'))
        except Exception as e:
            logger.exception(
                "Serializer validation encountered an unexpected error from service layer not derived from CustomAPIException.")
            raise serializers.ValidationError(
                {'non_field_errors': [f"服务层未知错误: {str(e)}"]},
                code="unexpected_internal_service_error"
            )

        return data

    def create(self, validated_data):
        if 'idempotent_result' in self.context:
            return self.context['idempotent_result'].data

        # 确保这里调用的是 Preliminary Service，它会创建并调度任务
        user = self.context['request'].user
        booking_preliminary_service = BookingPreliminaryService()

        pre_validate_data_for_service = {
            'space_id': validated_data.get('space_id'),
            'bookable_amenity_id': validated_data.get('bookable_amenity_id'),
            'start_time': validated_data['start_time'].isoformat(),
            'end_time': validated_data['end_time'].isoformat(),
            'purpose': validated_data['purpose'],
            'expected_attendees': validated_data['expected_attendees'],
            'booked_quantity': validated_data['booked_quantity'],
            'request_uuid': str(validated_data['request_uuid'])  # 确保是字符串
        }

        service_result = booking_preliminary_service.pre_validate(user, pre_validate_data_for_service)

        if service_result.success:
            return {
                'id': service_result.data.get('booking_id'),
                'request_uuid': service_result.data.get('request_uuid'),
                'status_code': service_result.status_code,  # 返回 preliminary service 的状态码 (如 202 ACCEPTED)
                'message': service_result.message
            }
        else:
            # 如果初步校验失败，pre_validate 已经抛出 ServiceException 并且转换为 CustomAPIException
            # 这里应该直接重新抛出对应的异常
            raise service_result.to_exception()


# --- 其他 Booking 相关序列化器 ---
class BookingMinimalSerializer(serializers.ModelSerializer):
    user_info = CustomUserMinimalSerializer(source='user', read_only=True)
    entity_name = serializers.SerializerMethodField()
    entity_type = serializers.SerializerMethodField()

    class Meta:
        model = BookingModel
        fields = [
            'id', 'request_uuid', 'user_info', 'entity_name', 'entity_type',
            'start_time', 'end_time', 'status', 'processing_status',
            'booked_quantity', 'created_at'
        ]
        read_only_fields = fields

    def get_entity_name(self, obj: BookingModel) -> Optional[str]:
        if obj.space:
            return obj.space.name
        elif obj.bookable_amenity and obj.bookable_amenity.amenity:
            return obj.bookable_amenity.amenity.name
        return None

    def get_entity_type(self, obj: BookingModel) -> Optional[str]:
        if obj.space:
            return 'Space'
        elif obj.bookable_amenity:
            return 'BookableAmenity'
        return None


class BookingDetailSerializer(serializers.ModelSerializer):
    # 重写关联字段，使其直接处理 Service 返回的字典数据 (无论 obj 是模型实例还是 CachedDictObject/纯字典)
    user = serializers.SerializerMethodField()
    space = serializers.SerializerMethodField()
    bookable_amenity = serializers.SerializerMethodField()
    related_space = serializers.SerializerMethodField()
    reviewed_by = serializers.SerializerMethodField()

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    processing_status_display = serializers.CharField(source='get_processing_status_display', read_only=True)

    # NEW: 添加 check_in_qrcode_url 字段
    check_in_qrcode_url = serializers.URLField(read_only=True, allow_null=True)

    class Meta:
        model = BookingModel
        fields = '__all__'
        read_only_fields = [
            'id', 'request_uuid', 'user', 'space', 'bookable_amenity',
            'status', 'processing_status', 'created_at', 'updated_at',
            'reviewed_by', 'reviewed_at', 'admin_notes', 'status_display',
            'processing_status_display', 'related_space', 'check_in_qrcode_url'
        ]

    # Helper methods to serialize the dict-based related objects or real model instances
    def _get_related_data_from_obj(self, obj, field_name: str, serializer_class: serializers.BaseSerializer):
        """通用辅助方法，根据 obj 类型获取相关数据并序列化。"""
        if isinstance(obj, BookingModel):  # If it's a real model instance
            related_instance = getattr(obj, field_name)
            return serializer_class(related_instance).data if related_instance else None
        else:  # Assumed to be dict-like (CachedDictObject or pure dict from .to_dict())
            related_data = getattr(obj, field_name, None)  # Access obj.user, obj.space etc directly
            # If related_data is already a dict, return it directly. No need to re-serialize.
            return related_data if isinstance(related_data, dict) else None

    def get_user(self, obj) -> Optional[Dict[str, Any]]:
        return self._get_related_data_from_obj(obj, 'user', CustomUserMinimalSerializer)

    def get_space(self, obj) -> Optional[Dict[str, Any]]:
        return self._get_related_data_from_obj(obj, 'space', SpaceMinimalSerializer)

    def get_bookable_amenity(self, obj) -> Optional[Dict[str, Any]]:
        return self._get_related_data_from_obj(obj, 'bookable_amenity', BookableAmenityMinimalSerializer)

    def get_related_space(self, obj) -> Optional[Dict[str, Any]]:
        return self._get_related_data_from_obj(obj, 'related_space', SpaceMinimalSerializer)

    def get_reviewed_by(self, obj) -> Optional[Dict[str, Any]]:
        return self._get_related_data_from_obj(obj, 'reviewed_by', CustomUserMinimalSerializer)

    def update(self, instance: BookingModel, validated_data: Dict[str, Any]) -> BookingModel:
        user = self.context['request'].user

        # 1. 检查不允许直接修改的字段 (这些是核心数据，任何用户都不能通过此接口直接修改)
        # 移除了 'user', 'status', 'processing_status'，因为它们现在由特定的逻辑处理
        read_only_on_update_immutable_fields = [
            'space', 'bookable_amenity', 'start_time', 'end_time', 'booked_quantity', 'request_uuid',
            'created_at', 'updated_at', 'admin_notes', 'reviewed_by', 'reviewed_at', 'check_in_qrcode'
        ]

        # 检查是否有尝试修改这些不可变字段
        for field in read_only_on_update_immutable_fields:
            if field in validated_data:
                # 使用 ForbiddenException 而不是 ValidationError，因为这是权限问题
                raise ForbiddenException(detail=f"字段 '{field}' 不允许通过此接口直接修改。",
                                         code='immutable_field_modification_forbidden')

        # 2. 特权用户 (系统管理员/超级用户) 拥有最高权限
        if user.is_system_admin or user.is_superuser:
            # 允许修改 'purpose' 和 'expected_attendees'
            instance.purpose = validated_data.get('purpose', instance.purpose)
            instance.expected_attendees = validated_data.get('expected_attendees', instance.expected_attendees)
            # 其他字段如果需要扩展给管理员，可以在这里逐一添加

            instance.save()
            return instance

        # 3. 空间管理员权限：不能修改详情，只能修改状态 (Status 字段已在 immutable_fields 中被拦截)
        #    如果空间管理员尝试修改任何非 immutable 字段，则拒绝。
        #    注意：这里假设 SpaceManager 已经在视图层被 `is_admin_or_space_manager_required` 装饰器通过
        #    要判断是否是管理当前预订相关空间的 SpaceManager
        is_space_manager_for_this_booking = (
                user.is_space_manager and instance.related_space and instance.related_space.managed_by_id == user.id
        )
        if user.is_space_manager and not is_space_manager_for_this_booking:
            raise ForbiddenException(
                detail="您是空间管理员但没有管理此预订相关空间的权限，因此不能修改预订详情。",
                code="spaceman_unauthorized_for_this_booking")

        if is_space_manager_for_this_booking:
            if validated_data:  # 如果有任何数据尝试更新，都拒绝
                raise ForbiddenException(
                    detail="空间管理员无权通过此接口修改预订详情，只能通过 /status/ 端点修改预订状态。",
                    code="spaceman_cannot_modify_booking_details"
                )

        # 4. 普通用户 (预订拥有者) 权限：
        if instance.user != user:
            raise ForbiddenException(detail="您没有权限修改此预订。", code='forbidden_to_modify_others_booking')

        # 只有在 'PENDING' 状态下才允许修改 'purpose'
        if instance.status != BookingModel.BOOKING_STATUS_PENDING:
            raise ForbiddenException(detail="只有待审核状态的预订才能修改目的。",
                                     code='booking_not_pending_for_purpose_edit')

        allowed_fields_for_owner_in_pending = ['purpose', 'expected_attendees']  # 允许普通用户修改 expected_attendees
        # 检查是否有未授权的字段被修改
        for field in validated_data.keys():
            if field not in allowed_fields_for_owner_in_pending:
                raise ForbiddenException(
                    detail=f"普通用户只能修改 'purpose' 和 'expected_attendees' 字段，且仅限待审核状态。字段 '{field}' 不允许修改。",
                    code='forbidden_field_modification_for_ordinary_user'
                )

        # 如果通过所有检查，则更新允许的字段
        instance.purpose = validated_data.get('purpose', instance.purpose)
        instance.expected_attendees = validated_data.get('expected_attendees', instance.expected_attendees)

        instance.save()
        return instance


class BookingStatusSerializer(serializers.Serializer):
    # This serializer directly works with dictionary data
    id = serializers.IntegerField(read_only=True, help_text="预订ID")
    request_uuid = serializers.UUIDField(read_only=True, help_text="请求唯一标识 UUID")
    processing_status = serializers.CharField(read_only=True, help_text="异步处理状态")
    processing_status_display = serializers.CharField(read_only=True, help_text="异步处理状态（可读）")
    status = serializers.CharField(read_only=True, help_text="业务状态")
    status_display = serializers.CharField(read_only=True, help_text="业务状态（可读）")
    admin_notes = serializers.CharField(read_only=True, allow_blank=True, help_text="管理员备注/错误信息")
    created_at = serializers.DateTimeField(read_only=True, help_text="创建时间")
    updated_at = serializers.DateTimeField(read_only=True, help_text="最近更新时间")


class BookingUpdateStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=BOOKING_STATUS_CHOICES_TUPLE,  # 使用导入的全局元组
        # help_text 同样使用导入的全局元组来动态生成
        help_text=f"新的预订状态 ({', '.join([c[0] for c in BOOKING_STATUS_CHOICES_TUPLE])})"
    )
    admin_notes = serializers.CharField(max_length=500, required=False, allow_blank=True, help_text="管理员备注")


# --- Violation Serializers ---

class ViolationSerializer(serializers.ModelSerializer):
    user = CustomUserMinimalSerializer(read_only=True, help_text="被违规用户")
    issued_by = CustomUserMinimalSerializer(read_only=True, help_text="违规记录发布者")
    resolved_by = CustomUserMinimalSerializer(read_only=True, help_text="违规处理者")
    booking_id = serializers.IntegerField(source='booking.id', read_only=True, help_text="关联预订ID")
    space_type = SpaceTypeMinimalSerializer(read_only=True, help_text="关联空间类型")

    raw_booking_id = serializers.PrimaryKeyRelatedField(
        queryset=BookingModel.objects.all(),  # 使用 BookingModel alias
        required=False, allow_null=True, write_only=True, source='booking',
        help_text="关联Booking的ID")
    raw_space_type_id = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), required=False, allow_null=True, write_only=True, source='space_type',
        help_text="关联空间类型ID (如果未通过Booking指定)")

    class Meta:
        model = Violation  # 使用导入的 Violation 模型
        fields = [
            'id', 'user', 'user_id',
            'booking', 'booking_id', 'raw_booking_id',
            'space_type', 'raw_space_type_id',
            'violation_type',  # ModelSerializer 会自动从 Violation 模型获取 choices=VIOLATION_TYPE_CHOICES
            'description', 'penalty_points',
            'is_resolved', 'resolved_at', 'resolved_by',
            'issued_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'issued_by', 'resolved_by', 'resolved_at', 'created_at', 'updated_at',
                            'booking']
        # 注意：此处不再需要显式定义 violation_type = serializers.ChoiceField(...)
        # 因为 ModelSerializer 已经通过 Meta.model = Violation 自动处理了 violation_type 字段的 choices。

    def create(self, validated_data):
        user_id = validated_data.pop('user_id')
        user_instance = CustomUser.objects.get(id=user_id)

        raw_booking_instance = validated_data.pop('booking', None)
        raw_space_type_instance = validated_data.pop('space_type', None)

        validated_data['user'] = user_instance
        if raw_booking_instance:
            validated_data['booking'] = raw_booking_instance
        if raw_space_type_instance:
            validated_data['space_type'] = raw_space_type_instance

        if 'request' in self.context:
            validated_data['issued_by'] = self.context['request'].user

        if validated_data.get('booking') and not validated_data.get('space_type'):
            booking_obj: BookingModel = validated_data['booking']  # 使用 BookingModel alias
            if booking_obj.space and booking_obj.space.space_type:
                validated_data['space_type'] = booking_obj.space.space_type
            elif booking_obj.bookable_amenity and booking_obj.bookable_amenity.space and booking_obj.bookable_amenity.space.space_type:
                validated_data['space_type'] = booking_obj.bookable_amenity.space.space_type

        return super().create(validated_data)

    def update(self, instance, validated_data):
        raw_booking_instance = validated_data.pop('booking', None)
        raw_space_type_instance = validated_data.pop('space_type', None)

        if raw_booking_instance:
            instance.booking = raw_booking_instance
        if raw_space_type_instance:
            instance.space_type = raw_space_type_instance

        if 'is_resolved' in validated_data and validated_data['is_resolved'] and not instance.is_resolved:
            instance.resolved_at = timezone.now()
            if 'request' in self.context:
                instance.resolved_by = self.context['request'].user
        elif 'is_resolved' in validated_data and not validated_data['is_resolved'] and instance.is_resolved:
            instance.resolved_at = None
            instance.resolved_by = None

        instance.description = validated_data.get('description', instance.description)
        instance.penalty_points = validated_data.get('penalty_points', instance.penalty_points)
        instance.violation_type = validated_data.get('violation_type', instance.violation_type)

        instance.save()
        return instance


# --- Admin/Policy Serializers (UserPenaltyPointsPerSpaceTypeSerializer 及以下，仅修正了模型引用和类型提示) ---

class UserPenaltyPointsPerSpaceTypeSerializer(serializers.ModelSerializer):
    user = CustomUserMinimalSerializer(read_only=True)
    space_type = SpaceTypeMinimalSerializer(read_only=True)

    class Meta:
        model = UserPenaltyPointsPerSpaceType
        fields = '__all__'
        read_only_fields = ['id', 'user', 'space_type', 'current_penalty_points', 'last_violation_at',
                            'last_ban_trigger_at']


class SpaceTypeBanPolicySerializer(serializers.ModelSerializer):
    space_type = SpaceTypeMinimalSerializer(read_only=True, allow_null=True)

    class Meta:
        model = SpaceTypeBanPolicy
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class UserSpaceTypeBanSerializer(serializers.ModelSerializer):
    user = CustomUserMinimalSerializer(read_only=True)
    space_type = SpaceTypeMinimalSerializer(read_only=True, allow_null=True)
    ban_policy_applied = SpaceTypeBanPolicySerializer(read_only=True, allow_null=True)
    issued_by = CustomUserMinimalSerializer(read_only=True, allow_null=True)

    class Meta:
        model = UserSpaceTypeBan
        fields = '__all__'
        read_only_fields = ['id', 'user', 'space_type', 'ban_policy_applied', 'issued_by', 'created_at', 'updated_at']


class UserSpaceTypeBanCreateUpdateSerializer(serializers.ModelSerializer):
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), source='user', write_only=True, help_text="用户ID")
    space_type_id = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), source='space_type', write_only=True, required=False, allow_null=True,
        help_text="空间类型ID (可选，null表示全局)")
    ban_policy_applied_id = serializers.PrimaryKeyRelatedField(
        queryset=SpaceTypeBanPolicy.objects.all(), source='ban_policy_applied', write_only=True, required=False,
        allow_null=True, help_text="关联的禁用策略ID (可选)")
    issued_by_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), source='issued_by', write_only=True, required=False, allow_null=True,
        help_text="发布禁用管理员的ID (可选)")

    class Meta:
        model = UserSpaceTypeBan
        fields = [
            'id', 'user_id', 'space_type_id', 'start_date', 'end_date', 'reason',
            'ban_policy_applied_id', 'issued_by_id'
        ]
        extra_kwargs = {
            'start_date': {'required': True},
            'end_date': {'required': True},
            'reason': {'required': True}
        }


class UserSpaceTypeExemptionSerializer(serializers.ModelSerializer):
    user = CustomUserMinimalSerializer(read_only=True)
    space_type = SpaceTypeMinimalSerializer(read_only=True, allow_null=True)
    granted_by = CustomUserMinimalSerializer(read_only=True,
                                             allow_null=True)  # Changed from `issued_by` to `granted_by` as per model

    class Meta:
        model = UserSpaceTypeExemption
        fields = '__all__'
        read_only_fields = ['id', 'user', 'space_type', 'granted_by', 'granted_at']  # Updated to `granted_at`
        # Removed created_at, updated_at from read_only_fields if model does not have them.
        # Assuming `granted_at` is the auto_now_add for this model.
        # If created_at/updated_at exist, would add them back.


class UserSpaceTypeExemptionCreateUpdateSerializer(serializers.ModelSerializer):
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), source='user', write_only=True, help_text="用户ID")
    space_type_id = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), source='space_type', write_only=True, required=False, allow_null=True,
        help_text="空间类型ID (可选，null表示全局)")
    granted_by_id = serializers.PrimaryKeyRelatedField(  # Changed from `issued_by_id` to `granted_by_id`
        queryset=CustomUser.objects.all(), source='granted_by', write_only=True, required=False, allow_null=True,
        help_text="授权豁免管理员的ID (可选)")

    class Meta:
        model = UserSpaceTypeExemption
        fields = [
            'id', 'user_id', 'space_type_id', 'start_date', 'end_date', 'exemption_reason',
            # Changed `reason` to `exemption_reason`
            'granted_by_id'
        ]
        extra_kwargs = {
            'start_date': {'required': False, 'allow_null': True},  # Make optional as per model
            'end_date': {'required': False, 'allow_null': True},  # Make optional as per model
            'exemption_reason': {'required': True}  # Make required as per model
        }


class DailyBookingLimitSerializer(serializers.ModelSerializer):
    user_group = serializers.SerializerMethodField()
    space_type = SpaceTypeMinimalSerializer(read_only=True, allow_null=True)

    class Meta:
        model = DailyBookingLimit
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_user_group(self, obj: DailyBookingLimit) -> Optional[Dict[str, Any]]:
        if obj.group:  # Accessing `obj.group` not `obj.user_group`
            from django.contrib.auth.models import Group
            group_obj: Group = obj.group
            return {'id': group_obj.id, 'name': group_obj.name}
        return None


class DailyBookingLimitCreateUpdateSerializer(serializers.ModelSerializer):
    # Field name in serializer is `group_id` now, maps to model `group`
    group_id = serializers.PrimaryKeyRelatedField(  # Use PrimaryKeyRelatedField for group_id too
        queryset=Group.objects.all(), source='group', write_only=True, required=True,  # group is not optional in model
        help_text="用户组ID")
    space_type_id = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), source='space_type', write_only=True, required=False, allow_null=True,
        help_text="空间类型ID (可选，null表示全局)")

    class Meta:
        model = DailyBookingLimit
        fields = ['id', 'group_id', 'space_type_id', 'max_bookings', 'is_active', 'priority']
        # removed start_date, end_date as they are not in DailyBookingLimit model
        extra_kwargs = {
            'max_bookings': {'required': True, 'min_value': 0},
            'is_active': {'required': True},
            'priority': {'required': True, 'min_value': 0},
        }

    def validate(self, data):
        # The PrimaryKeyRelatedField already handles fetching the group instance.
        # We just need to ensure 'group_id' is present if required, which the field itself handles.
        # No additional Group.objects.get() logic is needed here.
        # The field 'group_id' now provides the 'group' instance in validated_data directly.

        # Original validation for start_date/end_date removed as they are not in the model.
        return data

    def create(self, validated_data):
        # The PrimaryKeyRelatedField already mapped 'group_id' to the 'group' instance
        # and 'space_type_id' to `space_type` instance. So no need to pop/map manually.
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Same for update
        return super().update(instance, validated_data)


# --- BookingMarkNoShowSerializer ---
class BookingMarkNoShowSerializer(serializers.Serializer):
    """
    用于批量标记预订为未到场并创建违规记录的请求数据序列化器。
    """
    pk_list = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=1,
        help_text="要标记为未到场并创建违规记录的预订ID列表"
    )