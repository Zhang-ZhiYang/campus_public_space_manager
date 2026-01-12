# users/jwt_views.py
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenBlacklistView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from core.utils.response import success_response, error_response  # , error_response # 移除 error_response 导入
from core.utils.constants import \
    HTTP_200_OK  # HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED, MSG_UNAUTHORIZED, CODE_UNAUTHORIZED
from rest_framework.exceptions import AuthenticationFailed  # Added import


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # 将用户的常用属性放入 token payload，方便前端解析
        token['username'] = user.username
        token['email'] = user.email
        token['user_id'] = user.id
        token['name'] = user.name  # 新增 name 字段
        token['full_name'] = user.get_full_name

        # 包含用户所属的组名（即角色名）
        token['groups'] = [group.name for group in user.groups.all()]

        # 将自定义的布尔型角色属性添加到 token payload
        token['is_system_admin'] = user.is_system_admin
        token['is_space_manager'] = user.is_space_manager
        token['is_teacher'] = user.is_teacher
        token['is_student'] = user.is_student
        token['is_staff_member'] = user.is_staff_member

        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        # 在响应数据中也直接包含用户常用信息
        data['user_id'] = self.user.id
        data['username'] = self.user.username
        data['email'] = self.user.email
        data['name'] = self.user.name  # 新增 name 字段
        data['full_name'] = self.user.get_full_name

        # 包含用户所属的组名
        data['groups'] = [group.name for group in self.user.groups.all()]

        # 包含自定义的布尔型角色属性
        data['is_system_admin'] = self.user.is_system_admin
        data['is_space_manager'] = self.user.is_space_manager
        data['is_teacher'] = self.user.is_teacher
        data['is_student'] = self.user.is_student  # Corrected: token was used instead of data
        data['is_staff_member'] = self.user.is_staff_member

        return data


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        # validator.is_valid(raise_exception=True) 会抛出 DRF APIException，
        # 进而被 custom_exception_handler 捕获并格式化，无需手动 try...except...error_response
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        return success_response(
            message="Login successful",
            data=data,
            status_code=HTTP_200_OK
        )


# CustomTokenRefreshView 保持不变 (类似处理)
class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)  # 抛出交由全局异常处理

        return success_response(
            message="Token refreshed successfully",
            data=serializer.validated_data,
            status_code=HTTP_200_OK
        )


# CustomTokenBlacklistView 保持不变 (类似处理)
class CustomTokenBlacklistView(TokenBlacklistView):
    def post(self, request, *args, **kwargs):
        # super().post 可能会返回 Response 对象，也可能在 JWT 内部抛出异常。
        # 如果 JWT 内部抛出异常（如 TokenError），则会被 custom_exception_handler 捕获。
        # 如果 super().post 返回一个带有错误状态码的 Response，我们需要捕获它并重新包装。
        try:
            response = super().post(request, *args, **kwargs)
            if response.status_code == 200:
                return success_response(
                    message="Logout successful",
                    data={},
                    status_code=HTTP_200_OK
                )
            else:
                # JWT Blacklist 视图的错误响应可能比较简单，我们手动包装一下以符合格式
                error_details = response.data if response.data else {"detail": "Unknown error during logout."}
                final_error_payload = {
                    "code": "logout_failed",
                    "message": "Logout failed or token invalid.",
                    "details": error_details
                }
                return error_response(
                    message="Logout failed or token invalid.",
                    error=final_error_payload,
                    status_code=response.status_code
                )
        except Exception as e:
            # 捕获所有其他异常并抛出，让 custom_exception_handler 处理
            raise e