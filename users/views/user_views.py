# users/views/user_views.py
from django.db import IntegrityError
from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response  # 导入 Response
from core.utils.response import success_response, error_response
from core.utils.exceptions import BadRequestException, ConflictException, ForbiddenException
from core.utils.constants import MSG_CREATED, MSG_SUCCESS, HTTP_200_OK, HTTP_201_CREATED, HTTP_400_BAD_REQUEST, \
    HTTP_403_FORBIDDEN
from users.models import CustomUser  # 移除了对 Role 的导入
from users.serializers import CustomUserSerializer, AdminUserUpdateSerializer, \
    UserRegistrationSerializer, UserProfileUpdateSerializer  # 移除了 RoleSerializer
# --- 关键修改：将 IsAdminOrSuperAdmin 替换为 IsSystemAdminOnly ---
from users.permissions import IsSystemAdminOnly, IsAdminOrSpaceManagerOrReadOnly

class UserRegisterView(generics.CreateAPIView):
    """
    用户注册 API 视图。
    允许未认证用户注册新账户，注册后默认将其添加到 '学生' Group。
    """
    queryset = CustomUser.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            user = self.perform_create(serializer)  # perform_create 返回 user 实例
            headers = self.get_success_headers(serializer.data)
            return success_response(
                message=MSG_CREATED,
                data=CustomUserSerializer(user).data,  # 返回完整用户数据，可能包含 groups 信息
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
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        # 允许通过 PK 获取，但必须是当前登录用户
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field  # 默认 'pk'
        if lookup_url_kwarg in self.kwargs:
            if self.request.user.pk != self.kwargs[lookup_url_kwarg]:
                raise ForbiddenException(detail="您没有权限访问其他用户的个人资料。")
        return self.request.user  # 始终返回当前请求的用户

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return UserProfileUpdateSerializer  # 普通用户更新自己的资料时使用
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
class UserAdminViewSet(viewsets.ModelViewSet):  # 重命名为 UserAdminViewSet
    """
    管理员对用户的 CRUD 操作（包含组/角色管理）。
    只允许系统管理员（包括is_superuser）使用。
    """
    queryset = CustomUser.objects.all()  # 不再需要 select_related('role')
    serializer_class = CustomUserSerializer
    permission_classes = [permissions.IsAuthenticated, IsSystemAdminOnly]  # 只有系统管理员可读写

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return AdminUserUpdateSerializer
        return CustomUserSerializer

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return success_response(
            message=MSG_SUCCESS,
            data=response.data,
            status_code=HTTP_200_OK
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return success_response(message=MSG_SUCCESS, data=serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()  # 调用 serializer.save() 创建用户

        return success_response(
            message=MSG_CREATED,
            data=self.get_serializer(user).data,  # 返回更新后的用户数据
            status_code=HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)  # perform_update 调用 serializer.save()

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return success_response(message=MSG_SUCCESS, data=serializer.data, status_code=HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs, partial=True)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return success_response(message=MSG_SUCCESS, data={}, status_code=HTTP_200_OK)