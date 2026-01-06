# spaces/apps.py
from django.apps import AppConfig

class SpacesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'spaces'

    def ready(self):
        from core.dao import DAOFactory
        from spaces.dao.space_dao import SpaceDAO,BookableAmenityDAO
        from spaces.dao.amenity_dao import AmenityDAO
        from spaces.dao.space_type_dao import SpaceTypeDAO


        from core.service import ServiceFactory
        from spaces.service.space_service import SpaceService
        from spaces.service.amenity_service import AmenityService
        from spaces.service.space_type_service import SpaceTypeService

        # 注册 DAOs
        DAOFactory.register_dao('space', SpaceDAO)
        DAOFactory.register_dao('amenity_type', AmenityDAO)
        DAOFactory.register_dao('space_type', SpaceTypeDAO)
        DAOFactory.register_dao('bookable_amenity', BookableAmenityDAO)

        # 注册 Services
        ServiceFactory.register_service(SpaceService)
        ServiceFactory.register_service(AmenityService)
        ServiceFactory.register_service(SpaceTypeService)