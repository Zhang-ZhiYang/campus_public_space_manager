# check_in/api/serializers.py
from typing import Optional

from rest_framework import serializers
from django.utils import timezone
from datetime import datetime
from bookings.models import Booking
from check_in.models import CheckInRecord
from spaces.models import CHECK_IN_METHOD_SELF, CHECK_IN_METHOD_STAFF, CHECK_IN_METHOD_HYBRID, CHECK_IN_METHOD_NONE, \
    CHECK_IN_METHOD_LOCATION
from users.models import CustomUser  # 导入 CustomUser 模型
from spaces.models import Space  # 导入 Space 模型


# 辅助序列化器（如果 `users.api.serializers` 中没有，则需要在此处定义）
class CustomUserMinimalSerializer(serializers.ModelSerializer):
    """用于序列化 CustomUser 模型的最小信息"""

    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'get_full_name']


class BaseCheckInSerializer(serializers.Serializer):
    """
    签到请求的基类序列化器，包含通用字段。
    经纬度使用字符串类型，因为 DecimalField 在请求数据中可能遇到格式问题，
    在 Service 层进行 Decimal 转换和验证更安全。
    """
    latitude = serializers.CharField(required=False, allow_null=True, allow_blank=True, help_text="签到时的地理纬度")
    longitude = serializers.CharField(required=False, allow_null=True, allow_blank=True, help_text="签到时的地理经度")
    photo = serializers.ImageField(required=False, allow_null=True, help_text="签到时上传的照片")
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True, help_text="签到备注")

    # 统一进行经纬度类型转换和范围验证
    def validate(self, data):
        latitude_str = data.get('latitude')
        longitude_str = data.get('longitude')

        if latitude_str is not None and latitude_str != '':
            try:
                data['latitude'] = float(latitude_str)
                if not (-90 <= data['latitude'] <= 90):
                    raise serializers.ValidationError({"latitude": "纬度必须在 -90 到 90 之间。"})
            except ValueError:
                raise serializers.ValidationError({"latitude": "无效的纬度格式。"})

        if longitude_str is not None and longitude_str != '':
            try:
                data['longitude'] = float(longitude_str)
                if not (-180 <= data['longitude'] <= 180):
                    raise serializers.ValidationError({"longitude": "经度必须在 -180 到 180 之间。"})
            except ValueError:
                raise serializers.ValidationError({"longitude": "无效的经度格式。"})

        return data


class QRCheckInSerializer(BaseCheckInSerializer):
    """
    扫码签到序列化器 (booking_pk 从 URL 路径获取)。
    """
    # 无需额外字段，继承 BaseCheckInSerializer
    pass


class ManualCheckInSerializer(BaseCheckInSerializer):
    """
    手动签到序列化器 (booking_pk 从 URL 路径获取)。
    手动签到通常强制要求定位信息。
    """
    latitude = serializers.CharField(required=True, allow_blank=False, help_text="签到时的地理纬度")
    longitude = serializers.CharField(required=True, allow_blank=False, help_text="签到时的地理经度")


class StaffCheckInPayloadSerializer(BaseCheckInSerializer):
    """
    工作人员批量签到请求体序列化器。
    """
    booking_pks = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=1,
        help_text="要进行签到操作的预订ID列表"
    )
    latitude = serializers.CharField(required=False, allow_null=True, allow_blank=True, help_text="签到时的地理纬度")
    longitude = serializers.CharField(required=False, allow_null=True, allow_blank=True, help_text="签到时的地理经度")
    photo = serializers.ImageField(required=False, allow_null=True, help_text="签到照片文件")


class CheckInRecordSerializer(serializers.ModelSerializer):
    """
    CheckInRecord 响应序列化器，用于返回签到详情。
    """
    # 嵌套显示关联对象
    user = CustomUserMinimalSerializer(read_only=True)
    checked_in_by = CustomUserMinimalSerializer(read_only=True)
    booking_id = serializers.IntegerField(source='booking.id', read_only=True)

    # 签到图片 URL，方便前端直接使用
    check_in_image_url = serializers.SerializerMethodField(read_only=True, allow_null=True)

    class Meta:
        model = CheckInRecord
        fields = [
            'id', 'booking_id', 'user', 'checked_in_by', 'check_in_time',
            'latitude', 'longitude',  # <--- 新增
            'check_in_method', 'is_valid', 'notes', 'check_in_image_url', 'created_at', 'updated_at'
        ]
        read_only_fields = fields

    def get_check_in_image_url(self, obj: CheckInRecord) -> Optional[str]:
        if obj.check_in_image and hasattr(obj.check_in_image, 'url'):
            if 'request' in self.context:
                return self.context['request'].build_absolute_uri(obj.check_in_image.url)
            return obj.check_in_image.url
        return None