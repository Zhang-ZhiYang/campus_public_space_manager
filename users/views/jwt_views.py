# users/jwt_views.py
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenBlacklistView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer

from core.utils.response import success_response, error_response
from core.utils.constants import MSG_SUCCESS, MSG_UNAUTHORIZED, HTTP_200_OK, HTTP_401_UNAUTHORIZED
from rest_framework.exceptions import AuthenticationFailed  # 用于捕获认证失败异常


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    自定义 TokenObtainPairSerializer，如果需要添加额外数据到 token payload 中，
    可以在此覆盖 get_token 方法。目前我们只继承。
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # 例如，你可以在这里添加自定义声明 (custom claims)
        # token['name'] = user.username
        # token['student_id'] = user.student_id
        return token


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    自定义登录视图，使其返回统一的 API 响应格式。
    """
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except AuthenticationFailed as e:
            # 捕获 DRF 内置的认证失败异常
            return error_response(
                message=MSG_UNAUTHORIZED,
                error={"code": "authentication_failed", "detail": str(e)},
                status_code=HTTP_401_UNAUTHORIZED
            )
        except Exception as e:
            # 其他验证或处理异常
            return error_response(
                message=MSG_UNAUTHORIZED,
                error={"code": "invalid_credentials", "detail": str(e)},
                status_code=HTTP_401_UNAUTHORIZED
            )

        # 如果验证成功，使用我们的统一成功响应格式
        return success_response(
            message=MSG_SUCCESS,
            data=serializer.validated_data,  # 这里的 validated_data 包含了 access 和 refresh token
            status_code=HTTP_200_OK
        )


class CustomTokenRefreshView(TokenRefreshView):
    """
    自定义 token 刷新视图，使其返回统一的 API 响应格式。
    """

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            # 捕获刷新 token 时的各种异常（如 token 失效、黑名单等）
            return error_response(
                message=MSG_UNAUTHORIZED,
                error={"code": "token_refresh_failed", "detail": str(e)},
                status_code=HTTP_401_UNAUTHORIZED
            )

        return success_response(
            message=MSG_SUCCESS,
            data=serializer.validated_data,  # 这里的 validated_data 包含了新的 access token (可能还有 refresh token)
            status_code=HTTP_200_OK
        )


class CustomTokenBlacklistView(TokenBlacklistView):
    """
    自定义 token 黑名单（登出）视图，使其返回统一的 API 响应格式。
    """

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            # 捕获黑名单操作时的异常
            return error_response(
                message=MSG_UNAUTHORIZED,  # 或者更具体的错误，例如 "无效的刷新令牌"
                error={"code": "token_revoke_failed", "detail": str(e)},
                status_code=HTTP_401_UNAUTHORIZED
            )

        # 成功响应
        # Blacklist 视图默认返回 200 OK，没有响应体。
        # 我们这里返回一个成功的空数据体，或根据需要返回特定信息。
        return success_response(
            message="成功登出",  # 消息可以更具体
            data={},  # 空数据体或表示成功的简单字典
            status_code=HTTP_200_OK
        )