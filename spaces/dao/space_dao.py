# spaces/dao/space_dao.py
from django.db.models import QuerySet
from django.conf import settings
from guardian.shortcuts import get_objects_for_user
from typing import List, Tuple, Type, Any

from spaces.models import Space, SpaceType, BookableAmenity
# from bookings.models import Booking # 延迟导入
from core.dao import BaseDAO

CustomUser = settings.AUTH_USER_MODEL


class SpaceDAO(BaseDAO):
    model = Space

    def get_queryset(self) -> QuerySet[Space]:
        return super().get_queryset().select_related('space_type', 'parent_space', 'managed_by').prefetch_related('restricted_groups')

    def get_spaces_for_user_management(self, user: CustomUser) -> QuerySet[Space]:
        """
        Retrieves spaces that the given user has 'can_manage_space_details' permission for.
        Used for Admin list views.
        """
        if user.is_superuser or user.is_system_admin:
            return self.get_queryset()

        # Guardian's get_objects_for_user requires a class or a queryset.
        # We pass self.get_queryset() as klass to restrict it to already pre-fetched/selected related data if any.
        return get_objects_for_user(user, 'spaces.can_manage_space_details', klass=self.get_queryset())

    def space_has_children(self, space: Space) -> bool:
        return space.child_spaces.exists()

    def space_has_bookings(self, space: Space, BookingModel: Type['Booking']) -> bool:
        """
        Checks if a space has any associated bookings.
        Requires BookingModel to be passed in to avoid circular dependency.
        """
        return BookingModel.objects.filter(space=space).exists()

    def space_amenities_have_bookings(self, space: Space, BookableAmenityModel: Type[BookableAmenity],
                                      BookingModel: Type['Booking']) -> bool:
        """
        Checks if any bookable amenity within a space has associated bookings.
        Requires BookableAmenityModel and BookingModel to be passed in.
        """
        # Assuming there's a reverse relation from Booking to BookableAmenity, like `related_name='bookings'`
        # So it would be `bookable_amenity__bookings__isnull=False` when querying Booking
        # Or, if BookableAmenity has a direct Foreign Key to Booking, use that.
        # For simplicity, let's assume BookableAmenity has a reverse 'booking_set' or similar
        return BookableAmenityModel.objects.filter(space=space, booking__isnull=False).exists()


# BookableAmenity DAO for Inline, needs to be separate to be registered
class BookableAmenityDAO(BaseDAO):
    model = BookableAmenity

    def get_queryset(self) -> QuerySet[BookableAmenity]:
        return super().get_queryset().select_related('amenity', 'space__space_type')

    def get_bookable_amenities_for_space(self, space: Space) -> QuerySet[BookableAmenity]:
        """Retrieves bookable amenities for a specific space."""
        return self.get_queryset().filter(space=space)