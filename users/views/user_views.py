# users/views.py
from django.db import IntegrityError
from rest_framework import generics, permissions, status, viewsets # <-- 导入 viewsets

from core.utils.response import success_response, error_response
from core.utils.exceptions import BadRequestException, ConflictException, UnauthorizedException, ForbiddenException
from core.utils.constants import MSG_CREATED, MSG_SUCCESS, MSG_BAD_REQUEST, HTTP_200_OK, HTTP_201_CREATED, \
    HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from users.models import CustomUser, Role
from users.serializers import CustomUserSerializer, AdminUserUpdateSerializer, RoleSerializer, \
    UserRegistrationSerializer, UserProfileUpdateSerializer

class UserRegisterView(generics.CreateAPIView):
    """
    用户注册 API 视图。
    允许未认证用户注册新账户，注册后默认分配 '学生' 角色。
    """
    queryset = CustomUser.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]

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
        obj = self.request.user
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        if lookup_url_kwarg in self.kwargs:
            if obj.pk != self.kwargs[lookup_url_kwarg]:
                raise ForbiddenException(detail="You do not have permission to access another user's profile.")
        return obj

    def get_serializer_class(self):
        if self.request.method == 'PUT' or self.request.method == 'PATCH':
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

# --- 新增：管理员用户管理视图集 ---
class IsAdminOrReadOnly(permissions.BasePermission):
    """
    自定义权限：只允许管理员进行写操作，其他用户只读。
    """

    def has_permission(self, request, view):
        # 允许 GET, HEAD, OPTIONS 请求给所有认证用户
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated

        # 否则，只允许管理员或超级用户进行写操作 (POST, PUT, PATCH, DELETE)
        # 这里的 is_admin 属性需要 CustomUser 模型中定义
        return request.user and (request.user.is_staff or request.user.is_superuser or request.user.is_admin)

# 将 generis.ListCreateRetrieveUpdateDestroyAPIView 替换为 viewsets.ModelViewSet
class UserRoleAdminViewSet(viewsets.ModelViewSet): # <-- 关键修改
    """
    管理员对用户的 CRUD 操作（包含角色管理）。
    只允许管理员使用。
    """
    queryset = CustomUser.objects.all().select_related('role')  # 优化查询，预加载 role
    serializer_class = CustomUserSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]  # 认证用户，且管理员可写

    def get_serializer_class(self):
        # 对于 ModelViewSet，根据 action 来判断（list, retrieve, create, update, partial_update, destroy）
        if self.action in ['create', 'update', 'partial_update']: # <-- 使用 self.action
            return AdminUserUpdateSerializer
        return CustomUserSerializer  # GET (list, retrieve) 请求时仍使用 CustomUserSerializer 展示完整信息

    # 以下方法覆盖 ModelViewSet 的默认实现，以返回统一响应格式
    def list(self, request, *args, **kwargs):
        # 调用 ModelViewSet 的默认 list 方法，它会处理分页并返回一个 Response 对象
        response = super().list(request, *args, **kwargs)
        # 将默认响应的数据封装到我们的 success_response 中
        return success_response(
            message=MSG_SUCCESS,
            data=response.data, # ModelViewSet's list response.data already contains paginated data
            status_code=HTTP_200_OK
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return success_response(message=MSG_SUCCESS, data=serializer.data)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs) # 调用父类的 create 方法
        # 将父类返回的 Response 数据和头部封装
        return success_response(
            message=MSG_CREATED,
            data=response.data,
            status_code=HTTP_201_CREATED,
            headers=response.headers # 确保 Location header 被传递
        )

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs) # 调用父类的 update 方法
        return success_response(message=MSG_SUCCESS, data=response.data, status_code=HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        response = super().partial_update(request, *args, **kwargs) # 调用父类的 partial_update 方法
        return success_response(message=MSG_SUCCESS, data=response.data, status_code=HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        super().destroy(request, *args, **kwargs) # 调用父类的 destroy 方法
        return success_response(message=MSG_SUCCESS, data={}, status_code=HTTP_200_OK)  # 删除成功也返回统一格式

# 新增：角色管理视图 (如果需要通过 API 管理角色)
class RoleListView(generics.ListAPIView):
    """
    列出所有可用角色。
    """
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]  # 只有管理员能修改，但所有认证用户可以读

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return success_response(message=MSG_SUCCESS, data=serializer.data)