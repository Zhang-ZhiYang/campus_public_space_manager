# bookings/api/filters.py
import django_filters
from django.utils import timezone
from django_filters import DateFilter, DateTimeFilter, NumberFilter, CharFilter, UUIDFilter
from django.db.models import Q
from bookings.models import (
    Booking,  # 直接导入 Booking 模型
    Violation,
    UserPenaltyPointsPerSpaceType,
    SpaceTypeBanPolicy,
    UserSpaceTypeBan,
    UserSpaceTypeExemption,
    DailyBookingLimit,
    # 导入全局定义的 Choices 元组，用于 filter 的 help_text
    BOOKING_STATUS_CHOICES_TUPLE,  # <-- 更名并直接导入全局元组
    PROCESSING_STATUS_CHOICES_TUPLE,  # <-- 更名并直接导入全局元组
    VIOLATION_TYPE_CHOICES  # <-- 直接导入全局元组
)
from users.models import CustomUser
from django.contrib.auth.models import Group  # 导入 Group 模型，因为 DailyBookingLimit 引用了它
from spaces.models import Space, SpaceType, BookableAmenity  # 用于关联筛选


class BookingFilter(django_filters.FilterSet):
    """
    Booking 模型的过滤器。
    支持按用户、空间、设施、状态、时间范围、处理状态等进行筛选。
    """
    user = NumberFilter(field_name='user__pk', lookup_expr='exact', help_text="用户ID")
    space = NumberFilter(field_name='space__pk', lookup_expr='exact', help_text="直接预订的空间ID")
    bookable_amenity = NumberFilter(field_name='bookable_amenity__pk', lookup_expr='exact',
                                    help_text="直接预订的设施实例ID")

    related_space = NumberFilter(field_name='related_space__pk', lookup_expr='exact', help_text="关联父空间ID")

    status = CharFilter(field_name='status', lookup_expr='exact',
                        help_text=f"预订状态 ({', '.join([c[0] for c in BOOKING_STATUS_CHOICES_TUPLE])})")
    processing_status = CharFilter(field_name='processing_status', lookup_expr='exact',
                                   help_text=f"处理状态 ({', '.join([c[0] for c in PROCESSING_STATUS_CHOICES_TUPLE])})")
    start_time_after = DateTimeFilter(field_name='start_time', lookup_expr='gte',
                                      help_text="预订开始时间晚于 (ISO 8601)")
    start_time_before = DateTimeFilter(field_name='start_time', lookup_expr='lte',
                                       help_text="预订开始时间早于 (ISO 8601)")
    end_time_after = DateTimeFilter(field_name='end_time', lookup_expr='gte', help_text="预订结束时间晚于 (ISO 8601)")
    end_time_before = DateTimeFilter(field_name='end_time', lookup_expr='lte', help_text="预订结束时间早于 (ISO 8601)")
    created_at_after = DateTimeFilter(field_name='created_at', lookup_expr='gte', help_text="创建时间晚于 (ISO 8601)")
    created_at_before = DateTimeFilter(field_name='created_at', lookup_expr='lte', help_text="创建时间早于 (ISO 8601)")
    purpose_contains = CharFilter(field_name='purpose', lookup_expr='icontains', help_text="用途包含关键词")
    request_uuid = UUIDFilter(field_name='request_uuid', lookup_expr='exact', help_text="请求唯一标识 UUID")

    # NEW: 添加 is_overdue_for_review 过滤器
    is_overdue_for_review = django_filters.BooleanFilter(
        method='filter_is_overdue_for_review',
        help_text="是否是 '过时未处理/待审核' 的预订。True表示开始时间已过但仍处于待审核状态的预订。"
    )

    def filter_is_overdue_for_review(self, queryset, name, value):
        """
        过滤出开始时间已过，但状态仍为 PENDING、SUBMITTED 或 IN_PROGRESS 的预订。
        """
        if value:
            # 预订的开始时间早于当前时间
            # 并且状态是 PENDING (待审核)
            # 并且处理状态是 SUBMITTED 或 IN_PROGRESS (尚未完成最终决策)
            # 或者处理状态是 CREATED 但业务状态仍是 PENDING (等待人工审批但时间已过)
            return queryset.filter(
                Q(start_time__lt=timezone.now()), # 预订开始时间已过
                Q(status=Booking.BOOKING_STATUS_PENDING), # 业务状态是待审核
                Q(processing_status__in=[
                    Booking.PROCESSING_STATUS_SUBMITTED,
                    Booking.PROCESSING_STATUS_IN_PROGRESS,
                    Booking.PROCESSING_STATUS_CREATED # 比如Created但是需要人工审批
                ])
            )
        return queryset

    class Meta:
        model = Booking
        fields = [
            'user', 'space', 'bookable_amenity', 'related_space', 'status', 'processing_status',
            'start_time_after', 'start_time_before', 'end_time_after', 'end_time_before',
            'created_at_after', 'created_at_before', 'purpose_contains', 'request_uuid',
            'is_overdue_for_review' # NEW: 添加到 Meta.fields
        ]



class ViolationFilter(django_filters.FilterSet):
    """
    Violation 模型的过滤器。
    """
    user = NumberFilter(field_name='user__pk', lookup_expr='exact', help_text="被违规用户ID")
    booking = NumberFilter(field_name='booking__pk', lookup_expr='exact', help_text="关联预订ID")
    space_type = NumberFilter(field_name='space_type__pk', lookup_expr='exact', help_text="关联空间类型ID")
    violation_type = CharFilter(field_name='violation_type', lookup_expr='exact',
                                # 使用全局导入的元组
                                help_text=f"违规类型 ({', '.join([c[0] for c in VIOLATION_TYPE_CHOICES])})")
    is_resolved = django_filters.BooleanFilter(field_name='is_resolved', help_text="是否已解决")
    issued_by = NumberFilter(field_name='issued_by__pk', lookup_expr='exact', help_text="记录人ID")
    created_at_after = DateTimeFilter(field_name='created_at', lookup_expr='gte', help_text="创建时间晚于 (ISO 8601)")
    created_at_before = DateTimeFilter(field_name='created_at', lookup_expr='lte', help_text="创建时间早于 (ISO 8601)")

    class Meta:
        model = Violation
        fields = [
            'user', 'booking', 'space_type', 'violation_type', 'is_resolved', 'issued_by',
            'created_at_after', 'created_at_before'
        ]


class UserBanFilter(django_filters.FilterSet):
    """
    UserSpaceTypeBan 模型的过滤器。
    """
    user = NumberFilter(field_name='user__pk', lookup_expr='exact', help_text="被禁用用户ID")
    space_type = NumberFilter(field_name='space_type__pk', lookup_expr='exact',
                              help_text="关联空间类型ID (None表示全局)")
    is_active = django_filters.BooleanFilter(method='filter_is_active', help_text="是否活跃禁用 (True/False)")
    issued_by = NumberFilter(field_name='issued_by__pk', lookup_expr='exact', help_text="发布禁用人ID")
    start_date_after = DateTimeFilter(field_name='start_date', lookup_expr='gte',
                                      help_text="禁用开始时间晚于 (ISO 8601)")
    end_date_before = DateTimeFilter(field_name='end_date', lookup_expr='lte', help_text="禁用结束时间早于 (ISO 8601)")

    def filter_is_active(self, queryset, name, value):
        if value:  # is_active=True means end_date is in the future
            return queryset.filter(end_date__gt=timezone.now(), start_date__lte=timezone.now())
        else:  # is_active=False means end_date is past or start_date is future
            return queryset.filter(Q(end_date__lte=timezone.now()) | Q(start_date__gt=timezone.now()))

    class Meta:
        model = UserSpaceTypeBan
        fields = [
            'user', 'space_type', 'is_active', 'issued_by',
            'start_date_after', 'end_date_before'
        ]


class UserExemptionFilter(django_filters.FilterSet):
    """
    UserSpaceTypeExemption 模型的过滤器。
    """
    user = NumberFilter(field_name='user__pk', lookup_expr='exact', help_text="被豁免用户ID")
    space_type = NumberFilter(field_name='space_type__pk', lookup_expr='exact',
                              help_text="关联空间类型ID (None表示全局)")
    is_active = django_filters.BooleanFilter(method='filter_is_active', help_text="是否活跃豁免 (True/False)")
    # 修正字段名：模型中是 granted_by
    granted_by = NumberFilter(field_name='granted_by__pk', lookup_expr='exact', help_text="授权豁免人ID")
    start_date_after = DateTimeFilter(field_name='start_date', lookup_expr='gte',
                                      help_text="豁免开始时间晚于 (ISO 8601)")
    end_date_before = DateTimeFilter(field_name='end_date', lookup_expr='lte', help_text="豁免结束时间早于 (ISO 8601)")

    def filter_is_active(self, queryset, name, value):
        # 豁免活跃逻辑：start_date 为空或早于当前时间，并且 end_date 为空或晚于当前时间
        current_time = timezone.now()
        if value:  # is_active=True
            return queryset.filter(
                Q(start_date__isnull=True) | Q(start_date__lte=current_time),
                Q(end_date__isnull=True) | Q(end_date__gt=current_time)
            )
        else:  # is_active=False
            return queryset.filter(
                Q(start_date__gt=current_time) | Q(end_date__lte=current_time)
            )

    class Meta:
        model = UserSpaceTypeExemption
        fields = [
            'user', 'space_type', 'is_active', 'granted_by',  # 修正字段名 'issued_by' -> 'granted_by'
            'start_date_after', 'end_date_before'
        ]


class DailyBookingLimitFilter(django_filters.FilterSet):
    """
    DailyBookingLimit 模型的过滤器。
    """
    # 修正：user_group 更改为 group，因为模型中的字段名是 group
    group = NumberFilter(field_name='group__pk', lookup_expr='exact', help_text="用户组ID")
    space_type = NumberFilter(field_name='space_type__pk', lookup_expr='exact', help_text="空间类型ID (None表示全局)")
    # 修正：limit 更改为 max_bookings，因为模型中的字段名是 max_bookings
    max_bookings = NumberFilter(field_name='max_bookings', lookup_expr='exact', help_text="每日预订限制次数")
    is_active = django_filters.BooleanFilter(field_name='is_active', help_text="是否活跃")

    class Meta:
        model = DailyBookingLimit
        # 修正 fields 列表
        fields = ['group', 'space_type', 'max_bookings', 'is_active']


class SpaceTypeBanPolicyFilter(django_filters.FilterSet):
    """
    SpaceTypeBanPolicy 模型的过滤器。
    """
    space_type = NumberFilter(field_name='space_type__pk', lookup_expr='exact', help_text="空间类型ID (None表示全局)")
    threshold_points_gte = NumberFilter(field_name='threshold_points', lookup_expr='gte',
                                        help_text="违约点数阈值大于等于")
    threshold_points_lte = NumberFilter(field_name='threshold_points', lookup_expr='lte',
                                        help_text="违约点数阈值小于等于")
    is_active = django_filters.BooleanFilter(field_name='is_active', help_text="是否活跃")

    class Meta:
        model = SpaceTypeBanPolicy
        fields = [
            'space_type', 'threshold_points_gte', 'threshold_points_lte', 'is_active'
        ]