# users/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.user_views import UserProfileView, UserRegisterView, UserAdminViewSet  # 移除了 RoleListView

app_name = 'users'

# 初始化路由器
router = DefaultRouter()
# 注册管理员用户管理视图集，路径为 'admin/', basename='user-admin'
# 这将生成 /admin/ 和 /admin/<pk>/ 路由
router.register(r'admin', UserAdminViewSet, basename='user-admin')

urlpatterns = [
    # 用户注册接口
    path('register/', UserRegisterView.as_view(), name='register'),

    # 获取和更新当前用户个人资料接口
    # 允许通过 PK 获取，但视图逻辑会限制用户只能访问自己的 profile
    path('profile/<int:pk>/', UserProfileView.as_view(), name='profile-detail'),

    # 也可以提供一个无PK的 'me' 接口，用于获取当前登录用户资料
    path('profile/me/', UserProfileView.as_view(), name='my-profile'),

    # --- 包含路由器生成的 URL 模式 (用于管理员用户管理) ---
    # 管理员接口现在通过 /admin/ 路径访问
    path('', include(router.urls)),
]