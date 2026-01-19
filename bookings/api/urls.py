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

]