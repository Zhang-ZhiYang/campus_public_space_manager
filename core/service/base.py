# core/service/base.py
from core.dao import DAOFactory
from typing import Type, Dict, Any, Optional, List, Tuple

from core.service.service_result import ServiceResult
from core.utils.exceptions import CustomAPIException, ForbiddenException, NotFoundException, ConflictException

class BaseService:
    """
    Service 层基类，提供统一结果封装和通用异常处理逻辑。
    """
    _dao_map: Dict[str, str] = {} # 映射 service 名称到 DAO 名称，需要子类定义

    def __init__(self):
        self._daos = {}
        for service_attr, dao_name in self._dao_map.items():
            setattr(self, service_attr, DAOFactory.get_dao(dao_name))

    def _handle_exception(self, exc: Exception, default_message: str = "发生未知错误",
                          default_code: str = "service_error",
                          default_status_code: int = 500) -> ServiceResult[Any]:
        """
        统一处理 service 内部抛出的异常，并将其封装为 ServiceResult。
        """
        if isinstance(exc, CustomAPIException):
            return ServiceResult.error_result(
                message=exc.detail if isinstance(exc.detail, str) else default_message,
                errors=[str(exc.detail)] if isinstance(exc.detail, str) else list(exc.detail.values()) if isinstance(exc.detail, dict) else [default_message],
                error_code=exc.code,
                status_code=exc.status_code
            )
        elif isinstance(exc, PermissionError): # Python 内置的权限错误
            return ServiceResult.error_result(
                message=ForbiddenException.default_detail,
                errors=[str(exc)],
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )
        elif isinstance(exc, ValueError): # 通常是业务逻辑验证失败
            return ServiceResult.error_result(
                message="参数或业务逻辑验证失败",
                errors=[str(exc)],
                error_code="validation_error",
                status_code=400
            )
        elif isinstance(exc, self._get_model_does_not_exist_exception()): # 模型未找到
             return ServiceResult.error_result(
                message=NotFoundException.default_detail,
                errors=[str(exc)],
                error_code=NotFoundException.default_code,
                status_code=NotFoundException.status_code
            )
        else:
            # 记录未知异常
            import logging
            logger = logging.getLogger(self.__class__.__name__)
            logger.exception(f"Unhandled exception in service: {exc}")
            return ServiceResult.error_result(
                message=default_message,
                errors=[str(exc)],
                error_code=default_code,
                status_code=default_status_code
            )

    def _get_model_does_not_exist_exception(self):
        """
        尝试获取与当前服务相关的 DAO 模型的 DoesNotExist 异常。
        用于更精确地捕获模型未找到错误。
        """
        # 这是一个简化版本，假设每个 BaseService 只有一个主 DAO。
        # 如果需要更精确，可以遍历 _dao_map 中的所有 DAO 模型。
        if self._dao_map:
            first_dao_attr = next(iter(self._dao_map))
            dao_instance = getattr(self, first_dao_attr, None)
            if dao_instance and hasattr(dao_instance, 'model') and dao_instance.model:
                return dao_instance.model.DoesNotExist
        return None # 返回 None 或一个通用的异常（如 object does not exist）

    @classmethod
    def get_instance(cls):
        """
        返回当前Service的单例实例，通过ServiceFactory管理。
        """
        from .factory import ServiceFactory # 局部导入
        return ServiceFactory.get_service(cls.__name__)