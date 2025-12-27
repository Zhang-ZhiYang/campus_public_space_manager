from rest_framework.response import Response
# from rest_framework import status # 不再直接导入 status
from .constants import HTTP_200_OK, HTTP_400_BAD_REQUEST, MSG_SUCCESS, MSG_BAD_REQUEST  # 导入我们自己的状态码和消息常量


def api_response(success: bool, message: str, data=None, status_code: int = HTTP_200_OK, error=None):
    """
    统一的 API 响应格式。
    :param success: 操作是否成功 (bool)
    :param message: 返回给用户的消息 (str)
    :param data: 成功时返回的数据 (dict或list, 可选)
    :param status_code: HTTP 状态码 (int)
    :param error: 失败时返回的错误信息 (dict, 可选)
    :return: DRF Response 对象
    """
    resp_data = {
        "success": success,
        "message": message,
        "status_code": status_code,
    }
    if data is not None:
        resp_data["data"] = data
    if error is not None:
        resp_data["error"] = error

    return Response(resp_data, status=status_code)


def success_response(message: str = MSG_SUCCESS, data=None, status_code: int = HTTP_200_OK):
    """
    封装成功的 API 响应。
    """
    return api_response(True, message, data, status_code)


def error_response(message: str = MSG_BAD_REQUEST, error=None, status_code: int = HTTP_400_BAD_REQUEST):
    """
    封装失败的 API 响应。
    """
    return api_response(False, message, None, status_code, error)