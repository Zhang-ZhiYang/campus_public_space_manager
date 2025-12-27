# core/utils/response.py
from rest_framework.response import Response
from .constants import HTTP_200_OK, HTTP_400_BAD_REQUEST, MSG_SUCCESS, MSG_BAD_REQUEST


def api_response(success: bool, message: str, data=None, status_code: int = HTTP_200_OK, error=None, headers=None):
    """
    统一的 API 响应格式。
    :param success: 操作是否成功 (bool)
    :param message: 返回给用户的消息 (str)
    :param data: 成功时返回的数据 (dict或list, 可选)
    :param status_code: HTTP 状态码 (int)
    :param error: 失败时返回的错误信息 (dict, 可选)
    :param headers: HTTP 响应头 (dict, 可选), 将直接传递给 DRF Response。这是解决当前错误的关键。
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

    # 将 headers 参数传递给 DRF 的 Response 构造函数
    return Response(resp_data, status=status_code, headers=headers)


def success_response(message: str = MSG_SUCCESS, data=None, status_code: int = HTTP_200_OK, headers=None):
    """
    封装成功的 API 响应。
    现在接受 headers 参数，以便在创建成功等场景下添加 Location 头。
    """
    return api_response(True, message, data, status_code, headers=headers)


def error_response(message: str = MSG_BAD_REQUEST, error=None, status_code: int = HTTP_400_BAD_REQUEST):
    """
    封装失败的 API 响应。
    错误响应通常不需要额外的头部，因此不设 headers 参数。
    """
    # 错误响应通常不需要自定义Headers，所以这里不传递。
    return api_response(False, message, None, status_code, error)