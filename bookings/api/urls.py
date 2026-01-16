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
    # --- 预订 (Booking) ---
    path('bookings/create/', booking_create_views.BookingCreateAPIView.as_view(), name='booking-create'),
    path('bookings/', booking_list_views.BookingListAPIView.as_view(), name='booking-list'), # 用户自己的预订列表
    path('bookings/<int:pk>/', booking_retrieve_updates_views.BookingRetrieveUpdateDestroyAPIView.as_view(), name='booking-detail-update-delete'),

    # --- 预订状态操作 (由管理员或空间经理执行) ---
    path('bookings/<int:pk>/cancel/', booking_action_views.BookingCancelAPIView.as_view(), name='booking-cancel'),
    path('bookings/<int:pk>/check-in/', booking_action_views.BookingCheckInAPIView.as_view(), name='booking-check-in'),
    path('bookings/<int:pk>/check-out/', booking_action_views.BookingCheckOutAPIView.as_view(), name='booking-check-out'),
    path('bookings/<int:pk>/mark-no-show/', booking_action_views.BookingMarkNoShowAPIView.as_view(), name='booking-mark-no-show'),
    path('bookings/<int:pk>/approve/', booking_action_views.BookingApproveAPIView.as_view(), name='booking-approve'),
    path('bookings/<int:pk>/reject/', booking_action_views.BookingRejectAPIView.as_view(), name='booking-reject'),

    # --- 违约记录 (Violation) ---
    path('violations/', booking_list_views.ViolationListCreateAPIView.as_view(), name='violation-list-create'), # 管理员查看所有/创建
    path('violations/<int:pk>/', booking_retrieve_updates_views.ViolationRetrieveUpdateDestroyAPIView.as_view(), name='violation-detail-update-delete'),
    path('violations/resolve/', booking_action_views.ViolationMarkResolvedAPIView.as_view(), name='violation-mark-resolved'), # 批量解决

    # --- 禁用策略 (BanPolicy) ---
    path('ban-policies/', booking_list_views.BanPolicyListCreateAPIView.as_view(), name='ban-policy-list-create'), # 管理员查看所有/创建
    path('ban-policies/<int:pk>/', booking_retrieve_updates_views.BanPolicyRetrieveUpdateDestroyAPIView.as_view(), name='ban-policy-detail-update-delete'),

    # --- 每日预订限制 (DailyBookingLimit) ---
    path('daily-limits/', booking_list_views.DailyBookingLimitListCreateAPIView.as_view(), name='daily-limit-list-create'), # 管理员查看所有/创建
    path('daily-limits/<int:pk>/', booking_retrieve_updates_views.DailyBookingLimitRetrieveUpdateDestroyAPIView.as_view(), name='daily-limit-detail-update-delete'),

    # --- 用户禁用 (UserBan) ---
    path('user-bans/', booking_list_views.UserBanListCreateAPIView.as_view(), name='user-ban-list-create'), # 管理员查看所有/创建
    path('user-bans/<int:pk>/', booking_retrieve_updates_views.UserBanRetrieveUpdateDestroyAPIView.as_view(), name='user-ban-detail-update-delete'),

    # --- 用户豁免 (UserExemption) ---
    path('user-exemptions/', booking_list_views.UserExemptionListCreateAPIView.as_view(), name='user-exemption-list-create'), # 管理员查看所有/创建
    path('user-exemptions/<int:pk>/', booking_retrieve_updates_views.UserExemptionRetrieveUpdateDestroyAPIView.as_view(), name='user-exemption-detail-update-delete'),
]