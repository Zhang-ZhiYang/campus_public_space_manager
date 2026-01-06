# core/service/factory.py
from typing import Dict, Type
from core.service.base import BaseService # 从 core.service 导入 BaseService

class ServiceFactory:
    """
    A Factory for managing and providing singleton instances of Services.
    Services must be registered with their class name before they can be retrieved.
    """
    _service_registry: Dict[str, Type[BaseService]] = {}
    _service_instances: Dict[str, BaseService] = {}

    @classmethod
    def register_service(cls, service_class: Type[BaseService]):
        """
        Registers a Service class with the factory using its class name as the key.
        Args:
            service_class (Type[BaseService]): The Service class to register.
        Raises:
            TypeError: If service_class does not inherit from BaseService.
            ValueError: If a Service with the same name is already registered.
        """
        name = service_class.__name__
        if not issubclass(service_class, BaseService):
            raise TypeError(f"Service class '{name}' must inherit from BaseService.")
        if name in cls._service_registry:
            raise ValueError(f"Service with name '{name}' is already registered.")
        cls._service_registry[name] = service_class

    @classmethod
    def get_service(cls, name: str) -> BaseService:
        """
        Retrieves a singleton instance of the registered Service.
        Args:
            name (str): The class name of the Service.
        Returns:
            BaseService: A singleton instance of the requested Service.
        Raises:
            ValueError: If the Service is not registered.
        """
        if name not in cls._service_registry:
            raise ValueError(f"Service '{name}' not registered.")

        if name not in cls._service_instances:
            # Instantiate the Service and cache it
            cls._service_instances[name] = cls._service_registry[name]()
        return cls._service_instances[name]

    @classmethod
    def unregister_service(cls, name: str):
        """Unregisters a Service and removes its instance from the cache."""
        cls._service_registry.pop(name, None)
        cls._service_instances.pop(name, None)

    @classmethod
    def clear_registry(cls):
        """Clears all registered Services and instances. Primarily for testing."""
        cls._service_registry.clear()
        cls._service_instances.clear()