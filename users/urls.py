# users/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter  # <-- 导入 DefaultRouter
from . import views
from .views.user_views import RoleListView, UserProfileView, UserRegisterView, UserRoleAdminViewSet

app_name = 'users'

# 初始化路由器
router = DefaultRouter()
router.register(r'manage',UserRoleAdminViewSet, basename='user-manage')

urlpatterns = [
    # 用户注册 (修正 as_as_view -> as_view)
    path('register/', UserRegisterView.as_view(), name='register'),  # <-- 修正这里

    # 获取和更新当前用户个人资料
    path('profile/<int:pk>/', UserProfileView.as_view(), name='profile-detail'),

    # 角色列表接口
    path('roles/', RoleListView.as_view(), name='role-list'),

    # --- 包含路由器生成的 URL 模式 (用于管理员用户管理) ---
    path('', include(router.urls)),  # 这将为 UserRoleAdminViewSet 生成 /manage/ 和 manage/<pk>/ 路由
]