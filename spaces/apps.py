# spaces/apps.py
from django.apps import AppConfig

class SpacesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'spaces'

    def ready(self):
        # 导入 DAOFactory 和 ServiceFactory
        from core.dao import DAOFactory
        from core.service import ServiceFactory
        import spaces.signals # <--- 确保导入你的信号模块
        # 导入所有 DAO 类
        from spaces.dao.space_type_dao import SpaceTypeDAO
        from spaces.dao.amenity_dao import AmenityDAO
        from spaces.dao.space_dao import SpaceDAO, BookableAmenityDAO # BookableAmenityDAO 也在 SpaceDAO 文件中

        # 导入所有 Service 类
        from spaces.service.space_type_service import SpaceTypeService
        from spaces.service.amenity_service import AmenityService
        from spaces.service.space_service import SpaceService

        # 使用 DAOFactory 注册所有的 DAO
        # 确保键名（例如 'space_type'）在 service._dao_map 中有对应
        DAOFactory.register_dao('space_type', SpaceTypeDAO)
        DAOFactory.register_dao('amenity', AmenityDAO)
        DAOFactory.register_dao('space', SpaceDAO)
        DAOFactory.register_dao('bookable_amenity', BookableAmenityDAO)

        # 使用 ServiceFactory 注册所有的 Service
        ServiceFactory.register_service(SpaceTypeService)
        ServiceFactory.register_service(AmenityService)
        ServiceFactory.register_service(SpaceService)