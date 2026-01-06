# spaces/admin/__init__.py

from .space_type_admin import SpaceTypeAdmin
from .amenity_admin import AmenityAdmin
from .space_admin import SpaceAdmin, BookableAmenityInline # BookableAmenityInline 也需要在这里被引用