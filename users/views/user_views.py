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
from rest_framework.views import APIView


class UserRegisterView(generics.CreateAPIView):
    """
    用户注册 API 视图。
    允许未认证用户注册新账户，注册后默认将其添加到 '学生' Group。
    """
    queryset = CustomUser.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]  # 注册接口允许所有人访问

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            user = self.perform_create(serializer)
            headers = self.get_success_headers(serializer.data)
            return success_response(
                message=MSG_CREATED,
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
            # 否则抛出原始异常（或更通用的 InternalServerError）
            raise


class UserProfileView(generics.RetrieveUpdateAPIView):
    """
    用户个人资料获取与更新 API 视图。
    用户只能查看和更新自己的资料。
    """
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    permission_classes = [permissions.IsAuthenticated]  # 必须认证

    def get_object(self):
        # 允许通过 PK 获取，但必须是当前登录用户
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field  # 通常是 'pk'

        # 如果 URL 中提供了 PK，则检查 PK 是否与当前用户匹配
        if lookup_url_kwarg in self.kwargs:
            if str(self.request.user.pk) != str(self.kwargs[lookup_url_kwarg]):
                raise ForbiddenException(detail="您没有权限访问其他用户的个人资料。")

        # 始终返回当前请求的用户 (或其PK匹配)
        return self.request.user

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
        instance = self.get_object()  # 确保获取的是当前用户的对象
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
    AdminUserUpdateSerializer 内部包含详细的权限校验。
    """
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    permission_classes = [permissions.IsAuthenticated]  # 认证用户才能访问，具体权限由装饰器和序列化器内部逻辑控制

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            # 对于管理员更新用户，需要在序列化器中传入 request context
            return AdminUserUpdateSerializer
        return CustomUserSerializer

    def get_serializer_context(self):
        """
        在序列化器中传入 request context，以便进行权限验证。
        """
        return {'request': self.request, 'view': self}

    @is_system_admin_required
    def list(self, request, *args, **kwargs):

        qs = self.get_queryset()
        serializer = self.get_serializer(qs, many=True)
        return success_response(
            message=MSG_SUCCESS,
            data=serializer.data,
            status_code=HTTP_200_OK
        )

    @is_system_admin_required
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return success_response(message=MSG_SUCCESS, data=serializer.data)

    @is_system_admin_required
    def create(self, request, *args, **kwargs):
        # 序列化器内部会进行权限验证，例如系统管理员不能直接创建新的系统管理员
        serializer = self.get_serializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        # 在这里执行创建操作
        user = serializer.save()

        return success_response(
            message=MSG_CREATED,
            data=self.get_serializer(user).data,  # 使用 CustomUserSerializer 返回完整数据
            status_code=HTTP_201_CREATED
        )

    @is_system_admin_required
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()  # 获取要更新的用户实例
        # 序列化器内部会进行权限验证
        serializer = self.get_serializer(instance, data=request.data, partial=partial,
                                         context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return success_response(message=MSG_SUCCESS, data=self.get_serializer(instance).data, status_code=HTTP_200_OK)

    @is_system_admin_required
    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs, partial=True)

    @is_system_admin_required
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        # (非超级用户) 不能删除超级用户或另一个系统管理员 ---
        if getattr(request.user, 'is_system_admin', False) and not request.user.is_superuser:
            if instance.is_superuser:
                raise ForbiddenException(detail="系统管理员不能删除超级用户。")
            if getattr(instance, 'is_system_admin', False) and instance.pk != request.user.pk:  # 不能删除其他系统管理员
                raise ForbiddenException(detail="系统管理员不能删除其他系统管理员用户。")

        self.perform_destroy(instance)
        return success_response(message=MSG_SUCCESS, data={}, status_code=HTTP_200_OK)


class UserRoleView(APIView):
    """
    返回当前认证用户的角色信息（基于 groups 与 model 属性判断）。
    GET /api/v1/users/role/  (需要认证)
    返回示例：
    {
      "role": "space_manager",
      "is_system_admin": false,
      "is_space_manager": true,
      "is_check_in_staff": false,
      "groups": ["空间管理员"]
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        try:
            groups = list(user.groups.values_list('name', flat=True))
        except Exception:
            groups = []

        # 决定角色优先级： system_admin > checkin_staff > space_manager > normal
        role = 'normal'
        if getattr(user, 'is_system_admin', False):
            role = 'system_admin'
        elif getattr(user, 'is_check_in_staff', False) or getattr(user, 'is_staff_member', False) and not user.is_space_manager:
            # 若为签到员且非空间管理员，则判定为签到员
            if user.is_check_in_staff:
                role = 'checkin_staff'
        elif getattr(user, 'is_space_manager', False):
            role = 'space_manager'

        data = {
            'role': role,
            'is_system_admin': getattr(user, 'is_system_admin', False),
            'is_space_manager': getattr(user, 'is_space_manager', False),
            'is_check_in_staff': getattr(user, 'is_check_in_staff', False),
            'is_staff_member': getattr(user, 'is_staff_member', False),
            'groups': groups,
            'user': user.to_dict_minimal() if hasattr(user, 'to_dict_minimal') else {
                'id': user.pk,
                'username': user.username
            }
        }

        return success_response(message=MSG_SUCCESS, data=data, status_code=HTTP_200_OK)