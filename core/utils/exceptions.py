from rest_framework.exceptions import APIException
# from rest_framework import status # 不再直接导入 status
from .constants import (
    HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND, HTTP_409_CONFLICT, HTTP_503_SERVICE_UNAVAILABLE,
    MSG_BAD_REQUEST, MSG_UNAUTHORIZED, MSG_FORBIDDEN, MSG_NOT_FOUND, MSG_INTERNAL_ERROR
)

class CustomAPIException(APIException):
    """
    所有自定义 API 异常的基类。
    """
    status_code = HTTP_400_BAD_REQUEST
    default_detail = 'A custom error occurred.'
    default_code = 'custom_error'

    def __init__(self, detail=None, code=None, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        if detail is not None:
            self.detail = detail
        if code is not None:
            self.code = code
        else:
            self.code = self.default_code

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
    default_detail = 'The service is currently unavailable. Please try again later.'
    default_code = 'service_unavailable'

# 特定业务异常示例
class UserBlacklistedException(ForbiddenException):
    default_detail = 'This user is currently blacklisted and cannot perform this action.'
    default_code = 'user_blacklisted'

class BookingConflictException(ConflictException):
    default_detail = 'The requested time slot is already booked or conflicts with existing bookings.'
    default_code = 'booking_conflict'