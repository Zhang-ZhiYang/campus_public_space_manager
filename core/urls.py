from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# DRF Simple JWT 认证视图
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

# DRF Spectacular views for OpenAPI documentation
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

# 一个基本的 DRF DefaultRouter，用于将来注册 ViewSet；现在可以先放着
from rest_framework import routers
router = routers.DefaultRouter()

# 这里可以注册一些顶层的 ViewSet, 例如 UserViewSet
# router.register(r'users', UserViewSet) # 示例，待 users app 开发后再实际添加

urlpatterns = [
    # Django Admin 后台
    path('admin/', admin.site.urls),

    # DRF JWT 认证相关的 URL
    path('api/v1/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/token/verify/', TokenVerifyView.as_view(), name='token_verify'),

    # 你的自定义应用 API URL
    # 建议使用版本前缀方便管理 (e.g., /api/v1/users/)
    path('api/v1/', include(router.urls)), # 引入 DRF Router 的 URL
    path('api/v1/users/', include('users.urls')),          # 用户管理模块
    path('api/v1/spaces/', include('spaces.urls')),        # 空间管理模块
    path('api/v1/bookings/', include('bookings.urls')),    # 预订管理模块
    path('api/v1/notifications/', include('notifications.urls')), # 通知模块

    # DRF Spectacular (OpenAPI / Swagger 文档) URL
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    # 可视化的 Swagger UI (访问 /api/schema/swagger-ui/)
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    # 可视化的 Redoc UI (访问 /api/schema/redoc/)
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

# 仅在开发调试模式下，为静态文件和媒体文件提供服务
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)