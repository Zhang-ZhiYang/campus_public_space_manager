# bookings/api/views/__init__.py
# 导入所有具体的视图类，以便一次性导入到 urls.py
from .booking_action_views import *
from .booking_create_views import *
from .booking_list_views import *
from .booking_retrieve_updates_views import *