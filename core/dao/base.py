# core/dao/base.py
from django.db.models import Model, QuerySet, Manager
from typing import Type, Optional, List, Dict, Any

class BaseDAO:
    """
    Base Data Access Object (DAO) for Django models.
    Subclasses must define the `model` attribute.
    Provides common CRUD operations.
    """
    model: Type[Model] = None  # Must be overridden by subclasses

    def __init__(self):
        if not self.model:
            raise ValueError(
                f"DAO subclass {self.__class__.__name__} must define a 'model' attribute "
                "pointing to a Django Model."
            )

    @property
    def _manager(self) -> Manager[Model]:
        """Returns the default manager for the model."""
        return self.model.objects

    def get_queryset(self) -> QuerySet[Model]:
        """
        Returns the base queryset for the DAO.
        Subclasses can override this to add default select_related/prefetch_related or base filters.
        """
        return self._manager.all()

    def get_by_id(self, pk: int) -> Optional[Model]:
        """Retrieves a single object by its primary key."""
        try:
            return self.get_queryset().get(pk=pk)
        except self.model.DoesNotExist:
            return None

    def get_all(self) -> QuerySet[Model]:
        """Retrieves all objects."""
        return self.get_queryset()

    def filter(self, *args, **kwargs) -> QuerySet[Model]:
        """Filters objects based on given criteria."""
        return self.get_queryset().filter(*args, **kwargs)

    def create(self, **kwargs) -> Model:
        """Creates a new object."""
        return self._manager.create(**kwargs)

    def update(self, obj: Model, **kwargs) -> Model:
        """Updates an existing object with new data."""
        for key, value in kwargs.items():
            setattr(obj, key, value)
        obj.full_clean() # Optional: Run full validation
        obj.save()
        return obj

    def delete(self, obj: Model):
        """Deletes an object."""
        obj.delete()

    def bulk_create(self, objs: List[Model]) -> List[Model]:
        """Performs a bulk creation of objects."""
        return self._manager.bulk_create(objs)

    def bulk_update(self, objs: List[Model], fields: List[str]):
        """Performs a bulk update of objects."""
        self._manager.bulk_update(objs, fields)

    def count(self, *args, **kwargs) -> int:
        """Returns the count of objects matching criteria."""
        return self.get_queryset().filter(*args, **kwargs).count()