# # spaces/urls.py
# from django.urls import path, include
# from rest_framework.routers import DefaultRouter
# # 导入视图。如果视图文件中的权限或序列化器反过来导入了 urls，就可能造成循环导入。
# # 为了避免循环导入，通常将视图导入放在这里，并且视图文件不应该导入 urls。
# # 或者，可以采取'相对导入'的技巧，或者将视图模块中的类直接定义在 urls 文件中。
# # 在这里，我们假设 views.py 自身没有导入 urls.py。
# from . import views
# from .views import space_views, amenity_views  # 假设你的视图在 views/space_views.py 和 views/amenity_views.py 中
#
# app_name = 'spaces'
#
# # 创建一个路由器来自动生成 URL 模式
# router = DefaultRouter()
#
# # 注册空间相关的视图集
# router.register(r'spaces', space_views.SpaceViewSet, basename='space')
# router.register(r'space-types', space_views.SpaceTypeViewSet, basename='space-type')
# router.register(r'amenities', amenity_views.AmenityViewSet, basename='amenity')
# router.register(r'bookable-amenities', amenity_views.BookableAmenityViewSet, basename='bookable-amenity')
#
# urlpatterns = [
#     # 将路由器生成的 URL 模式包含进来
#     path('', include(router.urls)),
#
#     # 如果有其他非ViewSet的API，可以继续添加
#     # path('some-custom-api/', views.some_custom_view.as_view(), name='custom-api'),
# ]