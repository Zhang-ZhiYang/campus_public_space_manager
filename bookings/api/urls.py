# bookings/urls.py
from django.urls import path
from bookings.api.views import (
    BookingCreateAPIView,
    UserBookingListAPIView,
    BookingListAPIView,        # 新增
    BookingRetrieveAPIView,
    BookingCancelAPIView,
    BookingStatusUpdateAPIView # 新增
)

app_name = 'bookings' # 定义应用命名空间

urlpatterns = [
    # 用户操作
    path('create/', BookingCreateAPIView.as_view(), name='booking-create'),
    path('my-bookings/', UserBookingListAPIView.as_view(), name='my-booking-list'),
    path('<int:pk>/', BookingRetrieveAPIView.as_view(), name='booking-detail'),
    path('<int:pk>/cancel/', BookingCancelAPIView.as_view(), name='booking-cancel'),

    # 管理员操作
    path('all/', BookingListAPIView.as_view(), name='booking-list-all'), # 获取所有预订
    path('<int:pk>/status/', BookingStatusUpdateAPIView.as_view(), name='booking-status-update'), # 更新预订状态

]