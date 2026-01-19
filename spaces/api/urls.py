# spaces/api/urls.py
from django.urls import path
from spaces.api import views

app_name = 'spaces_api' # 定义应用命名空间

urlpatterns = [
    # --- Space 接口 (对所有用户可见，管理员操作根据权限) ---
    # 所有用户可查看空间列表，管理员和空间管理员可创建新空间
    path('spaces/', views.SpaceListCreateAPIView.as_view(), name='space-list-create'),
    # 所有用户可查看详情 (取决于其权限)，管理员和空间管理员可修改/删除 (取决于其权限)
    path('spaces/<int:pk>/', views.SpaceRetrieveUpdateDestroyAPIView.as_view(), name='space-detail-update-delete'),

    # --- 仅管理员GET接口：管理视角下的空间列表和详情 ---
    path('managed-spaces/', views.ManagedSpaceListCreateAPIView.as_view(), name='managed-space-list'), # 仅 GET
    path('managed-spaces/<int:pk>/', views.ManagedSpaceRetrieveUpdateDestroyAPIView.as_view(), name='managed-space-detail'), # 仅 GET

    # --- SpaceType 接口 (主要供系统管理员操作) ---
    path('space-types/', views.SpaceTypeListView.as_view(), name='space-type-list-create'),
    path('space-types/<int:pk>/', views.SpaceTypeDetailUpdateDestroyView.as_view(), name='space-type-detail-update-delete'),

    # --- Amenity 接口 (主要供系统管理员操作) ---
    path('amenities/', views.AmenityListView.as_view(), name='amenity-list-create'),
    path('amenities/<int:pk>/', views.AmenityDetailUpdateDestroyView.as_view(), name='amenity-detail-update-delete'),
]