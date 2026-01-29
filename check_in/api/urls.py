# check_in/api/urls.py
from django.urls import path
from check_in.api import views # 导入 views 模块

app_name = 'check_in_api' # 定义应用命名空间

urlpatterns = [
    # 执行签到接口 (POST)
    path('bookings/<int:booking_pk>/', views.CheckInAPIView.as_view(), name='perform-check-in'),
    # 获取预订签到记录详情接口 (GET)
    path('records/<int:booking_pk>/', views.CheckInRecordDetailAPIView.as_view(), name='check-in-record-detail'),
]