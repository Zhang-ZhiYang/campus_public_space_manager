# core/urls.py
from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

# 导入 Simple JWT 提供的序列化器 (如果 CustomTokenObtainPairSerializer 需要)
# from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenBlacklistView # 移除或注释掉这些默认导入

# 导入 DRF Spectacular 提供的视图 (保持不变)
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

# 导入我们自定义的 JWT 视图
from users.views.jwt_views import CustomTokenObtainPairView, CustomTokenRefreshView, CustomTokenBlacklistView

urlpatterns = [
    path('admin/', admin.site.urls),

    # --- DRF Spectacular for API documentation (保持不变) ---
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # --- API v1 路由 (保持不变) ---
    path('api/v1/users/', include('users.urls')),
    # path('api/v1/spaces/', include('spaces.urls')),
    # path('api/v1/bookings/', include('bookings.urls')),
    # path('api/v1/notifications/', include('notifications.urls')),

    # --- JWT 认证路由 (使用自定义视图) ---
    path('api/v1/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/token/blacklist/', CustomTokenBlacklistView.as_view(), name='token_blacklist'),

    path('register-test/', TemplateView.as_view(template_name='register_test.html'), name='register_test'),  # <-- 新增
    path('profile-page/', TemplateView.as_view(template_name='profile_page.html'), name='profile_page'),  # <-- 新增这一行
    path('login-test/', TemplateView.as_view(template_name='login_test.html'), name='login_test'),  # <-- 新增
]