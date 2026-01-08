# spaces/api/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter
from spaces.api import views

app_name = 'spaces_api' # 定义应用命名空间，与 core/urls.py 中的 include 保持一致

# Space 相关视图
urlpatterns = [
    # 空间列表 (通用用户) 和创建 (管理员)
    path('spaces/', views.SpaceListCreateAPIView.as_view(), name='space-list-create'),
    # 空间详情、更新、删除 (通用用户查看，管理员修改/删除)
    path('spaces/<int:pk>/', views.SpaceRetrieveUpdateDestroyAPIView.as_view(), name='space-detail-update-delete'),

    # 空间类型 (SpaceType) 列表、创建 (系统管理员)
    path('space-types/', views.SpaceTypeListView.as_view(), name='space-type-list-create'),
    path('space-types/<int:pk>/', views.SpaceTypeDetailUpdateDestroyView.as_view(), name='space-type-detail-update-delete'),

    # 设施类型 (Amenity) 列表、创建 (系统管理员)
    path('amenities/', views.AmenityListView.as_view(), name='amenity-list-create'),
    path('amenities/<int:pk>/', views.AmenityDetailUpdateDestroyView.as_view(), name='amenity-detail-update-delete'),

    # BookableAmenity 的 CRUD 如果需要，可以单独创建 ViewSet 或 APIViews
    # 但通常 BookableAmenity 作为 Space 的子资源来管理，通过 Space 的更新接口传入 amenity_ids 更常见
    # path('bookable-amenities/', views.BookableAmenityListCreateAPIView.as_view(), name='bookable-amenity-list-create'),
    # path('bookable-amenities/<int:pk>/', views.BookableAmenityRetrieveUpdateDestroyAPIView.as_view(), name='bookable-amenity-detail'),
]