# bookings/api/filters.py
import django_filters
from django.db.models import Q
from django.utils import timezone

from bookings.models import Booking, Violation, UserSpaceTypeBan, UserSpaceTypeExemption, DailyBookingLimit, \
    SpaceTypeBanPolicy
from users.models import CustomUser
from spaces.models import SpaceType, Space
from django.contrib.auth.models import Group


class BookingFilter(django_filters.FilterSet):
    # 示例过滤器，实际可根据需求扩展
    status = django_filters.ChoiceFilter(choices=Booking.BOOKING_STATUS_CHOICES, label="预订状态")
    user = django_filters.ModelChoiceFilter(queryset=CustomUser.objects.all(), label="预订用户 (ID)")
    space_id = django_filters.ModelChoiceFilter(queryset=Space.objects.all(), label="预订空间 (ID)")
    start_time_gte = django_filters.DateTimeFilter(field_name='start_time', lookup_expr='gte', label="开始时间 >= ")
    end_time_lte = django_filters.DateTimeFilter(field_name='end_time', lookup_expr='lte', label="结束时间 <= ")

    class Meta:
        model = Booking
        fields = ['status', 'user', 'space_id', 'bookable_amenity', 'start_time_gte', 'end_time_lte']


class ViolationFilter(django_filters.FilterSet):
    user = django_filters.ModelChoiceFilter(queryset=CustomUser.objects.all(), label="违约用户 (ID)")
    violation_type = django_filters.ChoiceFilter(choices=Violation.VIOLATION_TYPE_CHOICES, label="违约类型")
    is_resolved = django_filters.BooleanFilter(label="是否已解决")
    space_type = django_filters.ModelChoiceFilter(queryset=SpaceType.objects.all(), label="空间类型 (ID)")

    class Meta:
        model = Violation
        fields = ['user', 'violation_type', 'is_resolved', 'space_type']


class UserBanFilter(django_filters.FilterSet):
    user = django_filters.ModelChoiceFilter(queryset=CustomUser.objects.all(), label="被禁用用户 (ID)")
    space_type = django_filters.ModelChoiceFilter(queryset=SpaceType.objects.all(), label="空间类型 (ID)")
    is_active = django_filters.BooleanFilter(field_name='end_date', lookup_expr='gt', method='filter_is_active',
                                             label="是否活跃")

    def filter_is_active(self, queryset, name, value):
        if value:  # is_active=True, filter end_date > now
            return queryset.filter(end_date__gt=timezone.now())
        else:  # is_active=False, filter end_date <= now
            return queryset.filter(end_date__lte=timezone.now())

    class Meta:
        model = UserSpaceTypeBan
        fields = ['user', 'space_type', 'is_active']


class UserExemptionFilter(django_filters.FilterSet):
    user = django_filters.ModelChoiceFilter(queryset=CustomUser.objects.all(), label="豁免用户 (ID)")
    space_type = django_filters.ModelChoiceFilter(queryset=SpaceType.objects.all(), label="空间类型 (ID)")

    class Meta:
        model = UserSpaceTypeExemption
        fields = ['user', 'space_type']


class DailyBookingLimitFilter(django_filters.FilterSet):
    group = django_filters.ModelChoiceFilter(queryset=Group.objects.all(), label="用户组 (ID)")
    space_type = django_filters.ModelChoiceFilter(queryset=SpaceType.objects.all(), label="空间类型 (ID)")
    is_active = django_filters.BooleanFilter(label="是否启用")

    class Meta:
        model = DailyBookingLimit
        fields = ['group', 'space_type', 'is_active']


class BanPolicyFilter(django_filters.FilterSet):
    space_type = django_filters.ModelChoiceFilter(queryset=SpaceType.objects.all(), label="空间类型 (ID)")
    is_active = django_filters.BooleanFilter(label="是否启用")

    class Meta:
        model = SpaceTypeBanPolicy
        fields = ['space_type', 'is_active']