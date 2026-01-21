# bookings/api/urls.py
from django.urls import path, re_path
from .views import (
    BookingCreateAPIView,
    UserBookingListAPIView, AllBookingsListAPIView,
    BookingRetrieveUpdateDestroyAPIView,
    BookingCancelAPIView, BookingStatusUpdateAPIView,
    BookingMarkNoShowAPIView, BookingGetStatusAPIView
)

app_name = 'bookings_api'

urlpatterns = [
    # --- Booking Views ---
    path('bookings/', BookingCreateAPIView.as_view(), name='booking-create'),
    path('bookings/me/', UserBookingListAPIView.as_view(), name='user-booking-list'),
    path('bookings/all/', AllBookingsListAPIView.as_view(), name='all-booking-list'), # 管理员视角
    path('bookings/<int:pk>/', BookingRetrieveUpdateDestroyAPIView.as_view(), name='booking-detail'),

    # --- Booking Actions Views ---
    path('bookings/<int:pk>/cancel/', BookingCancelAPIView.as_view(), name='booking-cancel'),
    path('bookings/<int:pk>/status/', BookingStatusUpdateAPIView.as_view(), name='booking-status-update'), # 管理员更新状态
    path('bookings/mark-no-show/', BookingMarkNoShowAPIView.as_view(), name='booking-mark-no-show'), # 管理员批量标记未到场

    # Booking Status Query (by ID or UUID)
    path('bookings/<int:pk>/query-status/', BookingGetStatusAPIView.as_view(), name='booking-query-status-by-id'),
    re_path(r'bookings/(?P<request_uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/query-status/',
            BookingGetStatusAPIView.as_view(), name='booking-query-status-by-uuid'),

]