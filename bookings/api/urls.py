# bookings/urls.py
from django.urls import path
from bookings.api.views import BookingCreateAPIView

app_name = 'bookings' # 定义应用命名空间

urlpatterns = [
    # 这里将放置预订相关的 API 路由，例如发起预订、取消预订、查看我的预订等
    # path('', views.BookingListView.as_view(), name='booking-list'),
    path('create/', BookingCreateAPIView.as_view(), name='booking-create'),

]