# check_in/admin.py (更新版)

from django.contrib import admin
from django.db.models import Q
from guardian.admin import GuardedModelAdmin
# from guardian.shortcuts import get_objects_for_user # 空间管理员不再访问签到，所以无需此导入
from django.utils.html import format_html
from django.conf import settings
from django.contrib import messages

from check_in.models import CheckInRecord
from bookings.models import Booking

# 确保 spaces 模块已正确安装和配置，否则需要处理 ImportError
try:
    from spaces.models import Space, SpaceType, BookableAmenity

    SPACES_MODELS_LOADED = True
except ImportError:
    SPACES_MODELS_LOADED = False


    class Space:
        def __init__(self): self.name = "Mock Space"; self.id = 0


    class BookableAmenity:
        def __init__(self): self.name = "Mock Amenity"; self.id = 0; self.space = Space()


    print(
        "Warning: Could not import Space models in check_in.admin. Using dummy mocks. Admin functionality will be limited.")


@admin.register(CheckInRecord)
class CheckInRecordAdmin(GuardedModelAdmin):
    list_display = (
        'id', 'booking_id_display', 'user_display', 'checked_in_by_display',
        'check_in_time', 'check_in_method', 'is_valid', 'notes', 'check_in_image_thumbnail'
    )
    list_filter = (
        'check_in_method', 'is_valid', 'check_in_time',
        'booking__status',
        'booking__space__space_type',
        'booking__space',
        'booking__bookable_amenity__amenity',
        'user',
        'checked_in_by',
    )
    search_fields = (
        'booking__id__exact',
        'user__username', 'user__first_name', 'user__last_name',
        'checked_in_by__username', 'checked_in_by__first_name', 'checked_in_by__last_name',
        'notes',
        'booking__space__name',
        'booking__bookable_amenity__space__name',
        'booking__bookable_amenity__amenity__name'
    )
    raw_id_fields = ('booking', 'user', 'checked_in_by')
    date_hierarchy = 'check_in_time'

    fieldsets = (
        (None, {'fields': (('booking', 'user', 'checked_in_by'), ('check_in_time', 'check_in_method', 'is_valid'))}),
        ('地理信息', {'fields': (('latitude', 'longitude'),)}),
        ('其他信息', {'fields': ('notes', 'check_in_image', 'check_in_image_thumbnail_fieldset')}),
        ('系统信息', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)})
    )
    readonly_fields = (
        'created_at', 'updated_at', 'check_in_time', 'check_in_image',
        'check_in_image_thumbnail_fieldset', 'booking', 'user', 'checked_in_by',
    )
    actions = []

    @admin.display(description='预订ID')
    def booking_id_display(self, obj: CheckInRecord):
        return obj.booking.pk if obj.booking else 'N/A'

    @admin.display(description='预订用户')
    def user_display(self, obj: CheckInRecord):
        return obj.user.get_full_name if obj.user else 'N/A'

    @admin.display(description='执行签到用户')
    def checked_in_by_display(self, obj: CheckInRecord):
        return obj.checked_in_by.get_full_name if obj.checked_in_by else 'N/A'

    @admin.display(description='签到图片')
    def check_in_image_thumbnail(self, obj):
        if obj.check_in_image and hasattr(obj.check_in_image, 'url'):
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-width: 100px; max-height: 100px;" /></a>',
                obj.check_in_image.url, obj.check_in_image.url
            )
        return "无"

    @admin.display(description='签到图片')
    def check_in_image_thumbnail_fieldset(self, obj):
        return self.check_in_image_thumbnail(obj)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_authenticated:
            return qs.none()

        # 超级用户和系统管理员可以查看所有签到记录 (通过 is_superuser 或特定的总览权限)
        if request.user.is_superuser or request.user.has_perm('users.can_view_all_system_data'):  # 假设系统管理员有此标识权限
            return qs.select_related('booking__user', 'booking__space', 'booking__bookable_amenity__amenity',
                                     'checked_in_by')

        # '签到员' 可以查看所有签到记录 (通过 'check_in.can_view_checkinrecord' 模型级权限)
        if request.user.has_perm('check_in.can_view_checkinrecord'):
            return qs.select_related('booking__user', 'booking__space', 'booking__bookable_amenity__amenity',
                                     'checked_in_by')

        # 空间管理员现在不能访问签到信息 (已不再在此过滤范围)

        # 普通用户只能看到自己作为预订人或签到人创建的记录
        return qs.filter(
            Q(user=request.user) | Q(checked_in_by=request.user)
        ).select_related('booking__user', 'booking__space', 'booking__bookable_amenity__amenity', 'checked_in_by')

    def has_module_permission(self, request):
        if not request.user.is_authenticated:
            return False
        # 超级用户/系统管理员总是可见
        if request.user.is_superuser or request.user.has_perm('users.can_view_all_system_data'):
            return True

        # 其他用户通过检查具体模型权限来决定是否可见模块
        # '签到员' 和其他有 'check_in.view_checkinrecord' 权限的用户可以看到模块
        return request.user.has_perm(f'{self.opts.app_label}.view_{self.opts.model_name}')

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated:
            return False
        # 超级用户/系统管理员可以查看所有
        if request.user.is_superuser or request.user.has_perm('users.can_view_all_system_data'):
            return True

        # 如果是列表视图 (obj is None)，则依赖 has_module_permission
        if obj is None:
            return self.has_module_permission(request)

        # '签到员' 可以查看所有具体的签到记录对象
        if request.user.has_perm('check_in.can_view_checkinrecord'):
            return True

        # 普通用户：只能查看自己作为预订人或签到人创建的记录
        return (obj.user == request.user) or (obj.checked_in_by == request.user)

    def has_add_permission(self, request):
        # 签到记录不应通过 Admin 后台手动添加。只有系统管理员和签到员可以有此权限
        if request.user.is_superuser or request.user.has_perm('users.can_view_all_system_data'):
            return True
        return request.user.has_perm(f'{self.opts.app_label}.add_{self.opts.model_name}')

    def has_change_permission(self, request, obj=None):
        # 签到记录不应通过 Admin 后台修改。只有系统管理员和签到员可以有此权限
        if request.user.is_superuser or request.user.has_perm('users.can_view_all_system_data'):
            return True
        return request.user.has_perm(f'{self.opts.app_label}.change_{self.opts.model_name}')

    def has_delete_permission(self, request, obj=None):
        # 签到记录不应通过 Admin 后台删除，除非有特别的业务需求。
        # 只有系统管理员和签到员可以有此权限
        if request.user.is_superuser or request.user.has_perm('users.can_view_all_system_data'):
            return True
        return request.user.has_perm(f'{self.opts.app_label}.delete_{self.opts.model_name}')