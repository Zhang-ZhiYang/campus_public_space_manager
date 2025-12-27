from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException, ValidationError, NotAuthenticated, AuthenticationFailed
from django.http import Http404
import logging

from .response import error_response
from .exceptions import CustomAPIException
from .constants import (
    HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR,
    MSG_VALIDATION_ERROR, MSG_UNAUTHORIZED, MSG_NOT_FOUND, MSG_INTERNAL_ERROR
)

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    全局异常处理函数，将所有 DRF 和自定义异常转换为统一的 API 错误响应格式。
    """
    response = exception_handler(exc, context)

    if response is not None:
        custom_status_code = response.status_code
        detail = response.data.get('detail', str(exc))
        code = response.data.get('code', None)

        if isinstance(exc, ValidationError):
            message = MSG_VALIDATION_ERROR
            formatted_error_detail = response.data
        elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
            message = MSG_UNAUTHORIZED  # 或更具体的信息
            formatted_error_detail = {"code": code if code else "authentication_failed", "detail": detail}
        elif isinstance(exc, APIException):
            message = exc.detail if isinstance(exc.detail, str) else "A DRF API error occurred."
            formatted_error_detail = {"code": exc.get_codes(), "detail": exc.detail}
        else:
            message = detail
            formatted_error_detail = {"code": code if code else "api_error", "detail": detail}

        return error_response(
            message=message,
            error=formatted_error_detail,
            status_code=custom_status_code
        )

    if isinstance(exc, Http404):
        return error_response(
            message=MSG_NOT_FOUND,
            error={"code": "not_found", "detail": "The requested URL was not found on this server."},
            status_code=HTTP_404_NOT_FOUND
        )

    if isinstance(exc, CustomAPIException):
        message = exc.detail if isinstance(exc.detail, str) else exc.default_detail
        error_detail = {"code": exc.code, "detail": exc.detail}
        return error_response(
            message=message,
            error=error_detail,
            status_code=exc.status_code
        )

    logger.exception(f"Unhandled exception: {exc}", exc_info=True)
    return error_response(
        message=MSG_INTERNAL_ERROR,
        error={"code": "server_error", "detail": str(exc)},
        status_code=HTTP_500_INTERNAL_SERVER_ERROR
    )