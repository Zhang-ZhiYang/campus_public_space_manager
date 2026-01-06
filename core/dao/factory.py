# core/dao/factory.py
from typing import Dict, Type
from .base import BaseDAO # Import BaseDAO from within core.dao

class DAOFactory:
    """
    A Factory for managing and providing singleton instances of DAOs.
    DAOs must be registered with a unique name before they can be retrieved.
    """
    _dao_registry: Dict[str, Type[BaseDAO]] = {}
    _dao_instances: Dict[str, BaseDAO] = {}

    @classmethod
    def register_dao(cls, name: str, dao_class: Type[BaseDAO]):
        """
        Registers a DAO class with the factory.
        Args:
            name (str): A unique name for the DAO.
            dao_class (Type[BaseDAO]): The DAO class to register.
        Raises:
            TypeError: If dao_class does not inherit from BaseDAO.
            ValueError: If a DAO with the same name is already registered.
        """
        if not issubclass(dao_class, BaseDAO):
            raise TypeError(f"DAO class '{dao_class.__name__}' must inherit from BaseDAO.")
        if name in cls._dao_registry:
            raise ValueError(f"DAO with name '{name}' is already registered.")
        cls._dao_registry[name] = dao_class

    @classmethod
    def get_dao(cls, name: str) -> BaseDAO:
        """
        Retrieves a singleton instance of the registered DAO.
        Args:
            name (str): The unique name of the DAO.
        Returns:
            BaseDAO: A singleton instance of the requested DAO.
        Raises:
            ValueError: If the DAO is not registered.
        """
        if name not in cls._dao_registry:
            raise ValueError(f"DAO '{name}' not registered.")

        if name not in cls._dao_instances:
            # Instantiate the DAO and cache it
            cls._dao_instances[name] = cls._dao_registry[name]()
        return cls._dao_instances[name]

    @classmethod
    def unregister_dao(cls, name: str):
        """Unregisters a DAO and removes its instance from the cache."""
        cls._dao_registry.pop(name, None)
        cls._dao_instances.pop(name, None)

    @classmethod
    def clear_registry(cls):
        """Clears all registered DAOs and instances. Primarily for testing."""
        cls._dao_registry.clear()
        cls._dao_instances.clear()