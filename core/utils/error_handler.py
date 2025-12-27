# core/utils/error_handler.py
from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException, ValidationError, NotAuthenticated, AuthenticationFailed, \
    PermissionDenied, NotFound  # 明确导入 PermissionDenied 和 NotFound
from django.http import Http404
import logging

from .response import error_response
from .exceptions import CustomAPIException, ForbiddenException  # 确保导入了 ForbiddenException
from .constants import (
    HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR,
    MSG_VALIDATION_ERROR, MSG_UNAUTHORIZED, MSG_NOT_FOUND, MSG_INTERNAL_ERROR, MSG_FORBIDDEN
)

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    全局异常处理函数，将所有 DRF 和自定义异常转换为统一的 API 错误响应格式。
    """
    response = exception_handler(exc, context)  # DRF 默认的异常处理结果

    # 如果 DRF 已经生成了响应，我们基于它进行格式化
    if response is not None:
        custom_status_code = response.status_code

        # 针对不同类型的 DRF 异常进行更精确的错误细节构建
        if isinstance(exc, ValidationError):
            message = MSG_VALIDATION_ERROR
            formatted_error_detail = response.data  # ValidationError 的 data 已经是详细错误字典
        elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
            message = MSG_UNAUTHORIZED
            # Simple JWT 或 DRF 默认的 Unauthenticated 响应可能只给 detail 字符串
            detail = response.data.get('detail', str(exc))
            code = response.data.get('code', 'authentication_failed')
            formatted_error_detail = {"code": code, "detail": detail}
        elif isinstance(exc, PermissionDenied):  # 捕获 DRF 的 PermissionDenied
            message = MSG_FORBIDDEN
            formatted_error_detail = {"code": "permission_denied", "detail": str(exc.detail)}
        elif isinstance(exc, NotFound):  # 捕获 DRF 的 NotFound
            message = MSG_NOT_FOUND
            formatted_error_detail = {"code": "not_found", "detail": str(exc.detail)}
        elif isinstance(exc, APIException):  # 处理其他所有 DRF APIException
            message = exc.detail if isinstance(exc.detail, str) else "A DRF API error occurred."
            # DRF 的 APIException.detail 可以是字符串或字典
            if isinstance(exc.detail, dict):
                first_key = next(iter(exc.detail))  # 获取第一个键
                code = exc.detail.get(first_key, {}).get('code', first_key) if first_key else exc.default_code
                detail = exc.detail
            else:
                code = exc.default_code  # 使用默认 code
                detail = exc.detail
            formatted_error_detail = {"code": code, "detail": detail}
        else:  # 捕获其他任何未明确处理的 DRF 响应
            message = response.data.get('detail', MSG_INTERNAL_ERROR)
            code = response.data.get('code', 'api_error')
            formatted_error_detail = {"code": code, "detail": response.data}

        return error_response(
            message=message,
            error=formatted_error_detail,
            status_code=custom_status_code
        )

    # 如果 DRF 默认处理器没有生成响应（例如标准 Django Http404 或未捕获的自定义异常）
    if isinstance(exc, Http404):
        return error_response(
            message=MSG_NOT_FOUND,
            error={"code": "not_found", "detail": "The requested URL was not found on this server."},
            status_code=HTTP_404_NOT_FOUND
        )

    # 特别处理我们自定义的 CustomAPIException 及其子类
    if isinstance(exc, CustomAPIException):
        message = exc.detail if isinstance(exc.detail, str) else exc.default_detail
        error_detail = {"code": exc.code, "detail": exc.detail}
        return error_response(
            message=message,
            error=error_detail,
            status_code=exc.status_code
        )

    # 捕获所有其他未被处理的异常，将其记录并转换为 500 错误
    logger.exception(f"Unhandled exception: {exc}", exc_info=True)
    return error_response(
        message=MSG_INTERNAL_ERROR,
        error={"code": "server_error", "detail": str(exc)},
        status_code=HTTP_500_INTERNAL_SERVER_ERROR
    )