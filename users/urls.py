# users/urls.py
from django.urls import path
from . import views
from .views.user_views import UserRegisterView, UserProfileView

app_name = 'users' # 定义应用命名空间

urlpatterns = [
    path('register/', UserRegisterView.as_view(), name='register'),
    path('profile/<int:pk>/', UserProfileView.as_view(), name='profile-detail'),

]