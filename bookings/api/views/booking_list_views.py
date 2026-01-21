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
        service_result = booking_service.get_all_bookings(user, filters=self.request.query_params)  # 传入filters给service
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
    permission_classes = [IsAuthenticated]  # <--- 修正：只保留 IsAuthenticated 权限类
    serializer_class = BookingMinimalSerializer
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = BookingFilter

    # --- 修正：将装饰器应用于 dispatch 方法 ---
    @is_admin_or_space_manager_required
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        booking_service = BookingService()
        # Service 层的 get_all_bookings 会根据用户的 isAdmin/isSpaceManager 角色返回合适的 QuerySet
        service_result = booking_service.get_all_bookings(user, filters=self.request.query_params)  # 传入filters
        if service_result.success:
            return service_result.data.order_by('-created_at')
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        self.request.successful_response_status = status.HTTP_200_OK
        return super().list(request, *args, **kwargs)