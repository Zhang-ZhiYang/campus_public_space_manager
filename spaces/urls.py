# spaces/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from spaces.views import AmenityViewSet, SpaceViewSet

router = DefaultRouter()
router.register(r'amenities', AmenityViewSet, basename='amenity')
router.register(r'spaces', SpaceViewSet, basename='space')

urlpatterns = [
    path('', include(router.urls)),
]