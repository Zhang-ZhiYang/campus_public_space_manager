# users/urls.py
from django.urls import path
from . import views

app_name = 'users' # 定义应用命名空间

urlpatterns = [
    # 这里将放置用户相关的 API 路由，例如注册、登录、个人资料等
    # path('register/', views.UserRegisterView.as_view(), name='register'),
]