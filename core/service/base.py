# core/service/base.py
import logging
from typing import Type, Dict, Any, Optional, List, Tuple

# 导入 Django 的 ValidationError 和 ObjectDoesNotExist，以便在服务层捕获模型验证错误和未找到错误
from django.core.exceptions import ValidationError as DjangoValidationError, ObjectDoesNotExist

from core.service.service_result import ServiceResult
from core.utils.exceptions import CustomAPIException, ForbiddenException, NotFoundException, ConflictException, ServiceException, BadRequestException, InternalServerError # 确保所有自定义异常都导入

logger = logging.getLogger(__name__) # 初始化 logger for the base service

class BaseService:
    """
    Service 层基类，提供统一结果封装和通用异常处理逻辑。
    """
    _dao_map: Dict[str, str] = {} # 映射 service 名称到 DAO 名称，需要子类定义

    def __init__(self):
        self._daos = {}
        for service_attr, dao_name in self._dao_map.items():
            # 使用 helper 函数获取 DAO 实例，有助于避免潜在的循环依赖问题
            setattr(self, service_attr, self._get_dao_instance(dao_name))

    def _get_dao_instance(self, dao_name: str):
        """
        获取 DAO 实例的辅助函数。
        局部导入 DAOFactory，有助于避免某些条件下的循环导入问题，并确保 DAO 惰性加载。
        """
        from core.dao import DAOFactory
        return DAOFactory.get_dao(dao_name)

    def _handle_exception(self, exc: Exception, default_message: str = "发生未知错误",
                          default_code: str = "service_error",
                          default_status_code: int = 500) -> ServiceResult[Any]:
        """
        统一处理 service 内部抛出的异常，并将其封装为 ServiceResult。
        """
        # 1. 优先处理我们自定义的 CustomAPIException 及其子类
        if isinstance(exc, CustomAPIException):
            return ServiceResult.error_result(
                message=exc.detail if isinstance(exc.detail, str) else exc.default_detail,
                errors=[str(exc.detail)] if isinstance(exc.detail, str) else (
                    [value for key, value in exc.detail.items()] if isinstance(exc.detail, dict) else [exc.default_detail]), # 确保 errors 是列表
                error_code=getattr(exc, 'code', exc.default_code),
                status_code=exc.status_code
            )
        # 2. 处理我们自定义的 ServiceException (如果 Service 层直接抛出基类的 ServiceException)
        elif isinstance(exc, ServiceException):
            return ServiceResult.error_result(
                message=exc.message,
                errors=exc.errors or [str(exc)], # 确保 errors 是列表
                error_code=exc.error_code,
                status_code=exc.status_code
            )
        # 3. 处理 Django 的模型验证错误 (例如 DAO 层中调用 model.full_clean() 失败)
        elif isinstance(exc, DjangoValidationError):
            errors_list = []
            if hasattr(exc, 'error_dict'): # 字段验证错误
                for field, field_errors in exc.error_dict.items():
                    # 恢复到前一个版本，包含字段名的错误格式
                    errors_list.extend([f"[{field}]: {str(err)}" for err in field_errors])
            elif hasattr(exc, 'message_dict'): # 非字段错误或表单错误
                for field, field_errors in exc.message_dict.items():
                    # 恢复到前一个版本，包含字段名的错误格式
                    errors_list.extend([f"[{field}]: {str(err)}" for err in field_errors])
            else: # 单个验证消息
                errors_list = [str(exc)]

            return ServiceResult.error_result(
                message="数据验证失败。",
                errors=errors_list,
                error_code=BadRequestException.default_code, # 400 Bad Request
                status_code=BadRequestException.status_code
            )
        # 4. Python 内置的权限错误 (例如文件访问权限等)
        elif isinstance(exc, PermissionError):
            return ServiceResult.error_result(
                message=ForbiddenException.default_detail,
                errors=[str(exc)],
                error_code=ForbiddenException.default_code,
                status_code=ForbiddenException.status_code
            )
        # 5. 通用的参数或业务逻辑验证失败 (非 Django ValidationError)
        elif isinstance(exc, ValueError):
            return ServiceResult.error_result(
                message="参数或业务逻辑验证失败。",
                errors=[str(exc)],
                error_code="validation_error",
                status_code=BadRequestException.status_code # 400 Bad Request
            )
        # 6. 模型未找到异常 (例如通过 DAO.get() 或 get_queryset().get() 找不到时)
        elif isinstance(exc, ObjectDoesNotExist): # 通用处理所有 DoesNotExist 异常 (包括 Model.DoesNotExist)
             return ServiceResult.error_result(
                message=NotFoundException.default_detail,
                errors=[str(exc)],
                error_code=NotFoundException.default_code,
                status_code=NotFoundException.status_code
            )
        # 7. 其他所有未被明确处理的未知异常
        else:
            logger.exception(f"Unhandled exception in service: {exc}", exc_info=True) # 记录完整堆栈信息
            return ServiceResult.error_result(
                message=default_message,
                errors=[str(exc)], # 确保 errors 是列表
                error_code="platform_error", # 修改为更通用的平台错误码
                status_code=default_status_code
            )

    def _get_model_does_not_exist_exception(self):
        """
        尝试获取与当前服务相关的 DAO 模型的 DoesNotExist 异常。
        用于更精确地捕获模型未找到错误。
        这个方法在 Python 3.6+ 中可以通过 `Model.DoesNotExist` 和 `ObjectDoesNotExist` 的父子关系来统一。
        所以，直接返回 `ObjectDoesNotExist` 是安全的。
        """
        # 如果 self._dao_map 存在且有至少一个 DAO 关联，理论上可以尝试获取其特定的 DoesNotExist
        if self._dao_map:
            first_dao_key = next(iter(self._dao_map))
            dao_instance = getattr(self, first_dao_key, None)
            if dao_instance and hasattr(dao_instance, 'model') and dao_instance.model:
                # 返回特定模型的 DoesNotExist 异常类，它是 ObjectDoesNotExist 的一个子类
                return dao_instance.model.DoesNotExist
        # 如果没有找到特定的模型异常，返回一个通用的 ObjectDoesNotExist 异常。
        # Django 的 ObjectDoesNotExist 是所有模型 DoesNotExist 的基类。
        return ObjectDoesNotExist

    @classmethod
    def get_instance(cls):
        """
        返回当前Service的单例实例，通过ServiceFactory管理。
        """
        from .factory import ServiceFactory # 局部导入
        return ServiceFactory.get_service(cls.__name__)