from django.db import IntegrityError
from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from users.models import CustomUser
from users.serializers import (
    CustomUserSerializer, UserRegistrationSerializer, UserProfileUpdateSerializer
)
from core.utils.response import success_response, error_response
from core.utils.exceptions import BadRequestException, ConflictException, UnauthorizedException, ForbiddenException
from core.utils.constants import MSG_CREATED, MSG_SUCCESS, MSG_BAD_REQUEST, HTTP_200_OK, HTTP_201_CREATED, \
    HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN


class UserRegisterView(generics.CreateAPIView):
    """
    用户注册 API 视图。
    允许未认证用户注册新账户。
    """
    queryset = CustomUser.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]  # 允许所有人访问

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            headers = self.get_success_headers(serializer.data)
            return success_response(
                message=MSG_CREATED,
                data=serializer.data,
                status_code=HTTP_201_CREATED,
                headers=headers
            )
        except BadRequestException as e:  # 捕获自定义 BadRequestException
            return error_response(message=e.detail, error=e.get_full_details(), status_code=e.status_code)
        except ConflictException as e:  # 捕获自定义 ConflictException (例如用户名/邮箱冲突)
            return error_response(message=e.detail, error=e.get_full_details(), status_code=e.status_code)
        except Exception as e:
            # 捕获数据库完整性错误，例如唯一性约束
            if isinstance(e, IntegrityError) and 'UNIQUE constraint failed' in str(e):
                if 'username' in str(e):
                    raise ConflictException(detail="Username already registered.", code="username_exists")
                elif 'email' in str(e):
                    raise ConflictException(detail="Email already registered.", code="email_exists")
                elif 'phone_number' in str(e):
                    raise ConflictException(detail="Phone number already registered.", code="phone_number_exists")
                elif 'student_id' in str(e):  # 新增学号唯一性冲突捕获
                    raise ConflictException(detail="Student ID already registered.", code="student_id_exists")
            # 其他未预料的错误会由全局异常处理器处理
            raise  # 重新抛出，让全局异常处理器捕获


class UserProfileView(generics.RetrieveUpdateAPIView):
    """
    用户个人资料获取与更新 API 视图。
    用户只能查看和更新自己的资料。
    """
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    permission_classes = [permissions.IsAuthenticated]  # 只允许认证用户访问

    def get_object(self):
        # obj 是当前认证的用户实例
        obj = self.request.user
        # 获取 URL 中的 PK 参数名称，默认为 'pk'
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field

        # 检查 URL 中是否提供了 PK 参数
        if lookup_url_kwarg in self.kwargs:
            if obj.pk != self.kwargs[lookup_url_kwarg]:  # <-- 这里是关键修正！
                raise ForbiddenException(detail="You do not have permission to access another user's profile.")
        return obj
    def get_serializer_class(self):
        if self.request.method == 'PUT' or self.request.method == 'PATCH':
            return UserProfileUpdateSerializer
        return CustomUserSerializer

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return success_response(message=MSG_SUCCESS, data=serializer.data, status_code=HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return success_response(message=MSG_SUCCESS, data=serializer.data, status_code=HTTP_200_OK)