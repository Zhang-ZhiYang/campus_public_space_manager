# core/utils/exceptions.py (MODIFIED)
from rest_framework.exceptions import APIException
# 确保导入所有常量
from .constants import (
    HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND, HTTP_409_CONFLICT, HTTP_500_INTERNAL_SERVER_ERROR, HTTP_503_SERVICE_UNAVAILABLE,
    MSG_BAD_REQUEST, MSG_UNAUTHORIZED, MSG_FORBIDDEN, MSG_NOT_FOUND, MSG_INTERNAL_ERROR,
    MSG_SERVICE_UNAVAILABLE
)

class CustomAPIException(APIException):
    """
    所有自定义 API 异常的基类。
    继承自 DRF 的 APIException，因此它会被 DRF 的异常处理器自动处理。
    """
    status_code = HTTP_400_BAD_REQUEST
    default_detail = 'A custom error occurred.'
    default_code = 'custom_error'

    # MODIFICATION: Add 'message' parameter and map it to detail if detail is not provided.
    def __init__(self, detail=None, code=None, status_code=None, message=None):
        _detail = detail if detail is not None else message # Prioritize detail, then message
        if _detail is None: # Fallback to default if both are empty
            _detail = self.default_detail

        _code = code if code is not None else self.default_code

        super().__init__(detail=_detail, code=_code)

        self.status_code = status_code if status_code is not None else self.status_code

class BadRequestException(CustomAPIException):
    status_code = HTTP_400_BAD_REQUEST
    default_detail = MSG_BAD_REQUEST
    default_code = 'bad_request'

class UnauthorizedException(CustomAPIException):
    status_code = HTTP_401_UNAUTHORIZED
    default_detail = MSG_UNAUTHORIZED
    default_code = 'unauthorized'

class ForbiddenException(CustomAPIException):
    status_code = HTTP_403_FORBIDDEN
    default_detail = MSG_FORBIDDEN
    default_code = 'permission_denied'

class NotFoundException(CustomAPIException):
    status_code = HTTP_404_NOT_FOUND
    default_detail = MSG_NOT_FOUND
    default_code = 'not_found'

class ConflictException(CustomAPIException):
    status_code = HTTP_409_CONFLICT
    default_detail = 'The request could not be completed due to a conflict with the current state of the resource.'
    default_code = 'conflict'

class ServiceUnavailableException(CustomAPIException):
    status_code = HTTP_503_SERVICE_UNAVAILABLE
    default_detail = MSG_SERVICE_UNAVAILABLE
    default_code = 'service_unavailable'

# 特定业务异常示例
class UserBlacklistedException(ForbiddenException):
    default_detail = 'This user is currently blacklisted and cannot perform this action.'
    default_code = 'user_blacklisted'

class InternalServerError(CustomAPIException):
    status_code = HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = MSG_INTERNAL_ERROR
    default_code = 'server_error'

class BookingConflictException(ConflictException):
    default_detail = 'The requested time slot is already booked or conflicts with existing bookings.'
    default_code = 'booking_conflict'

class ServiceException(Exception): # Base class remains Exception, no direct CustomAPIException inheritance here
    """
    Service层通用的异常基类，用于在服务层表示业务逻辑错误。
    此异常不继承自 CustomAPIException，因为 Service 层的异常不直接是 API 响应，
    而是由视图层捕获 ServiceResult.error_result 或此 ServiceException 后转换为 API 响应。
    """
    default_code = 'service_internal_error'
    status_code = HTTP_500_INTERNAL_SERVER_ERROR  # 默认HTTP状态码

    def __init__(self, message=MSG_INTERNAL_ERROR, error_code=None, status_code=None, errors=None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code if error_code is not None else self.default_code
        self.status_code = status_code if status_code is not None else self.status_code
        self.errors = errors if errors is not None else [message] if message else []

    def __str__(self):
        if self.errors and isinstance(self.errors, list):
            detail_str = "; ".join([str(err) for err in self.errors])
        elif self.message:
            detail_str = self.message
        else:
            detail_str = self.default_code

        return f"[{self.error_code}] {detail_str}"