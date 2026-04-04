# bookings/api/views/booking_list_views.py
import logging
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

from core.pagination import CustomPageNumberPagination
from core.decorators import is_admin_or_space_manager_required, is_admin_or_space_manager_for_qs_obj
from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError

from bookings.api.serializers import BookingMinimalSerializer
from bookings.api.filters import BookingFilter
from bookings.service.booking_service import BookingService  # 导入 BookingService

logger = logging.getLogger(__name__)

class UserBookingListAPIView(generics.ListAPIView):
    """
    列出当前认证用户的所有预订记录。
    支持筛选和分页。
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BookingMinimalSerializer
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = BookingFilter

    def get_queryset(self):
        user = self.request.user
        booking_service = BookingService()
        # 不在 Service 层预先应用 request.query_params 的过滤，交由 DjangoFilterBackend（filterset_class）处理。
        service_result = booking_service.get_all_bookings(user, filters=None)
        if service_result.success:
            return service_result.data.order_by('-created_at')  # 默认按创建时间倒序
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        # DRF 的 list 方法会自动处理分页和序列化，并通过 get_queryset() 获取数据
        # 我们只需在父方法执行前设置成功的 HTTP 状态码
        self.request.successful_response_status = status.HTTP_200_OK
        return super().list(request, *args, **kwargs)

class AllBookingsListAPIView(generics.ListAPIView):
    """
    列出所有预订记录（管理员视角）。
    只有系统管理员或空间管理员可以访问。支持筛选和分页。
    """
    permission_classes = [IsAuthenticated]  # <<--- 必须保留 IsAuthenticated 以触发认证
    serializer_class = BookingMinimalSerializer
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = BookingFilter

    # 此处无需 is_admin_or_space_manager_for_qs_obj 装饰器，因为是 ListAPIView，
    # 权限应在视图类级别和 Service 层 QuerySet 过滤中处理。

    @is_admin_or_space_manager_required # <<--- 将装饰器应用于目标方法(list)
    def list(self, request, *args, **kwargs):
        user = self.request.user # 此时 request.user 已经是认证用户
        booking_service = BookingService()
        # Service 层的 get_all_bookings 会根据用户的 isAdmin/isSpaceManager 角色返回合适的 QuerySet
        # 但不要传入 request.query_params，这样 DjangoFilterBackend 会使用 BookingFilter 对 queryset 进行筛选
        service_result = booking_service.get_all_bookings(user, filters=None)
        if service_result.success:
            self.request.successful_response_status = status.HTTP_200_OK # 放到这里，因为 list 可能被 get_queryset 的异常中断
            return super().list(request, *args, **kwargs) # 调用父类的 list 方法，它会使用 get_queryset
        else:
            raise service_result.to_exception()

    # 注意：get_queryset 方法的逻辑保持不变，因为 service_result.data 已经是经过权限过滤的 QuerySet。
    # decorated list method will handle the overall flow.
    # We moved the `self.request.successful_response_status = status.HTTP_200_OK` into the decorated list method.
    def get_queryset(self):
        user = self.request.user
        booking_service = BookingService()
        service_result = booking_service.get_all_bookings(user, filters=None)
        if service_result.success:
            return service_result.data.order_by('-created_at')
        else:
            raise service_result.to_exception()