# users/views/user_views.py
from django.db import IntegrityError
from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response
from core.utils.response import success_response, error_response
from core.utils.exceptions import BadRequestException, ConflictException, ForbiddenException
from core.utils.constants import MSG_CREATED, MSG_SUCCESS, HTTP_200_OK, HTTP_201_CREATED, HTTP_400_BAD_REQUEST, \
    HTTP_403_FORBIDDEN
from users.models import CustomUser
from users.serializers import CustomUserSerializer, AdminUserUpdateSerializer, \
    UserRegistrationSerializer, UserProfileUpdateSerializer
# 导入自定义权限装饰器
from core.decorators import is_system_admin_required

class UserRegisterView(generics.CreateAPIView):
    """
    用户注册 API 视图。
    允许未认证用户注册新账户，注册后默认将其添加到 '学生' Group。
    """
    queryset = CustomUser.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny] # 注册接口允许所有人访问

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            user = self.perform_create(serializer)
            headers = self.get_success_headers(serializer.data)
            return success_response(
                message=MSG_CREATED,
                data=CustomUserSerializer(user).data,
                status_code=HTTP_201_CREATED,
                headers=headers
            )
        except BadRequestException as e:
            return error_response(message=e.detail, error=e.get_full_details(), status_code=e.status_code)
        except ConflictException as e:
            return error_response(message=e.detail, error=e.get_full_details(), status_code=e.status_code)
        except Exception as e:
            if isinstance(e, IntegrityError) and 'UNIQUE constraint failed' in str(e):
                if 'username' in str(e):
                    raise ConflictException(detail="Username already registered.", code="username_exists")
                elif 'email' in str(e):
                    raise ConflictException(detail="Email already registered.", code="email_exists")
                elif 'phone_number' in str(e):
                    raise ConflictException(detail="Phone number already registered.", code="phone_number_exists")
                elif 'work_id' in str(e):
                    raise ConflictException(detail="Student ID already registered.", code="work_id_exists")
            raise

class UserProfileView(generics.RetrieveUpdateAPIView):
    """
    用户个人资料获取与更新 API 视图。
    用户只能查看和更新自己的资料。
    """
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    permission_classes = [permissions.IsAuthenticated] # 必须认证

    def get_object(self):
        # 允许通过 PK 获取，但必须是当前登录用户
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        if lookup_url_kwarg in self.kwargs:
            if self.request.user.pk != self.kwargs[lookup_url_kwarg]:
                raise ForbiddenException(detail="您没有权限访问其他用户的个人资料。")
        return self.request.user # 始终返回当前请求的用户

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return UserProfileUpdateSerializer # 普通用户更新自己的资料时使用
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

# --- 管理员用户管理视图集 ---
class UserAdminViewSet(viewsets.ModelViewSet):
    """
    管理员对用户的 CRUD 操作（包含组/角色管理）。
    只允许系统管理员（包括is_superuser）使用。
    """
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    permission_classes = [permissions.IsAuthenticated] # 认证用户才能访问，但具体操作需要装饰器权限

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return AdminUserUpdateSerializer
        return CustomUserSerializer

    @is_system_admin_required # 系统管理员才能列出所有用户
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return success_response(
            message=MSG_SUCCESS,
            data=response.data,
            status_code=HTTP_200_OK
        )

    @is_system_admin_required # 系统管理员才能检索特定用户
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return success_response(message=MSG_SUCCESS, data=serializer.data)

    @is_system_admin_required # 系统管理员才能创建用户
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return success_response(
            message=MSG_CREATED,
            data=self.get_serializer(user).data,
            status_code=HTTP_201_CREATED
        )

    @is_system_admin_required # 系统管理员才能更新用户
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return success_response(message=MSG_SUCCESS, data=serializer.data, status_code=HTTP_200_OK)

    @is_system_admin_required # 系统管理员才能部分更新用户
    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs, partial=True)

    @is_system_admin_required # 系统管理员才能删除用户
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return success_response(message=MSG_SUCCESS, data={}, status_code=HTTP_200_OK)