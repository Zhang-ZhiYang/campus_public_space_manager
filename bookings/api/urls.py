# bookings/api/urls.py
from django.urls import path, include
from bookings.api.views import (
    booking_action_views,
    booking_create_views,
    booking_list_views,
    booking_retrieve_updates_views,
)

app_name = 'bookings_api'  # 定义应用命名空间

urlpatterns = [

    # --- 预订状态操作 (取消、签到、签出等) ---
    path('bookings/<int:pk>/cancel/', booking_action_views.BookingCancelAPIView.as_view(), name='booking-cancel'),
    path('bookings/<int:pk>/check-in/', booking_action_views.BookingCheckInAPIView.as_view(), name='booking-check-in'),
    path('bookings/<int:pk>/check-out/', booking_action_views.BookingCheckOutAPIView.as_view(),
         name='booking-check-out'),
    path('bookings/<int:pk>/mark-no-show/', booking_action_views.BookingMarkNoShowAPIView.as_view(),
         name='booking-mark-no-show'),
    path('bookings/<int:pk>/approve/', booking_action_views.BookingApproveAPIView.as_view(), name='booking-approve'),
    path('bookings/<int:pk>/reject/', booking_action_views.BookingRejectAPIView.as_view(), name='booking-reject'),


    # 标记批量违约记录为已解决
    path('violations/resolve/', booking_action_views.ViolationMarkResolvedAPIView.as_view(),
         name='violation-mark-resolved'),

]