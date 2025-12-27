# users/jwt_views.py
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenBlacklistView

# ==========================================
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from core.utils.response import success_response, error_response
from core.utils.constants import HTTP_200_OK, HTTP_400_BAD_REQUEST

# ====================================================================
# JWT Token 视图 - 自定义响应格式
# ====================================================================

# 重写 TokenObtainPairSerializer 以在响应中包含 user_id 和其他用户信息
class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['username'] = user.username
        token['email'] = user.email
        token['user_id'] = user.id
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data['user_id'] = self.user.id
        data['username'] = self.user.username
        data['email'] = self.user.email
        if self.user.role:
            data['role'] = self.user.role.name
        else:
            data['role'] = None
        return data

class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            error_detail = e.detail if hasattr(e, 'detail') else (e.args[0] if e.args else "Authentication failed.")
            return error_response(
                message="Invalid credentials.",
                error=error_detail,
                status_code=HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        return success_response(
            message="Login successful",
            data=data,
            status_code=HTTP_200_OK
        )

class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            error_detail = e.detail if hasattr(e, 'detail') else (e.args[0] if e.args else "Token refresh failed.")
            return error_response(
                message="Token refresh failed.",
                error=error_detail,
                status_code=HTTP_400_BAD_REQUEST
            )
        return success_response(
            message="Token refreshed successfully",
            data=serializer.validated_data,
            status_code=HTTP_200_OK
        )

# === 关键修改：继承新的 TokenBlacklistView ===
class CustomTokenBlacklistView(TokenBlacklistView): # 注意这里使用了别名 _TokenBlacklistView
    def post(self, request, *args, **kwargs):
        # 父类 Blacklist view 成功时返回 200 OK 和一个空响应体
        # 如果你希望它返回统一的 success_response，需要捕获其行为
        # Simple JWT 的 TokenBlacklistView 默认成功时不返回 JSON 响应体，
        # 而是只有 200 OK 状态码。
        # 这里我们调用 super().post() 让它执行黑名单逻辑，
        # 然后我们再返回我们统一的 success_response。
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            return success_response(
                message="Logout successful",
                data={},
                status_code=HTTP_200_OK
            )
        # 如果父类返回非 200 的状态码 (不应该发生，但以防万一)
        return error_response(
            message="Logout failed",
            error=response.data if hasattr(response, 'data') else "Unknown error during logout.",
            status_code=response.status_code
        )