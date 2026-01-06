# spaces/dao/amenity_dao.py
from django.db.models import QuerySet
from spaces.models import Amenity
from core.dao import BaseDAO

class AmenityDAO(BaseDAO):
    model = Amenity

    def get_queryset(self) -> QuerySet[Amenity]:
        return super().get_queryset().all() # Simple all(), no complex related for Amenity Type