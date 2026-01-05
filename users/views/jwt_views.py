# users/jwt_views.py
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenBlacklistView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from core.utils.response import success_response, error_response
from core.utils.constants import HTTP_200_OK, HTTP_400_BAD_REQUEST

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # 将用户的常用属性放入 token payload，方便前端解析
        token['username'] = user.username
        token['email'] = user.email
        token['user_id'] = user.id
        token['first_name'] = user.first_name
        token['last_name'] = user.last_name
        token['full_name'] = user.get_full_name  # 包含 get_full_name
        # 包含用户所属的组名（即角色名）
        token['groups'] = [group.name for group in user.groups.all()]
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        # 在响应数据中也直接包含用户常用信息
        data['user_id'] = self.user.id
        data['username'] = self.user.username
        data['email'] = self.user.email
        data['first_name'] = self.user.first_name
        data['last_name'] = self.user.last_name
        data['full_name'] = self.user.get_full_name
        # 包含用户所属的组名
        data['groups'] = [group.name for group in self.user.groups.all()]

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


# CustomTokenRefreshView 保持不变
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


# CustomTokenBlacklistView 保持不变
class CustomTokenBlacklistView(TokenBlacklistView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            return success_response(
                message="Logout successful",
                data={},
                status_code=HTTP_200_OK
            )
        return error_response(
            message="Logout failed",
            error=response.data if hasattr(response, 'data') else "Unknown error during logout.",
            status_code=response.status_code
        )