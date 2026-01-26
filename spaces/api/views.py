# spaces/api/views.py

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from core.pagination import CustomPageNumberPagination

import logging

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError, NotFoundException, ForbiddenException

from core.utils.constants import MSG_CREATED, MSG_SUCCESS, HTTP_201_CREATED, HTTP_200_OK, HTTP_204_NO_CONTENT
from spaces.api.filters import SpaceFilter
from core.service.cache import CacheService, CachedDictObject

from spaces.service.space_service import SpaceService
from spaces.service.space_type_service import SpaceTypeService
from spaces.service.amenity_service import AmenityService

from spaces.api.serializers import (
    SpaceListSerializer, SpaceCreateUpdateSerializer, SpaceBaseSerializer,
    AmenityBaseSerializer, AmenityCreateUpdateSerializer,
    SpaceTypeBaseSerializer, SpaceTypeCreateUpdateSerializer
)

from core.decorators import is_system_admin_required, is_admin_or_space_manager_required, \
    is_admin_or_space_manager_for_qs_obj

from spaces.models import Amenity, Space, SpaceType

logger = logging.getLogger(__name__)


# --- Space API Views (通用用户和管理员都可访问其 CUD 操作，权限在 Service 和视图装饰器中控制) ---

class SpaceListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = SpaceFilter

    def get_serializer_class(self):
        if self.request.method == 'POST':
            # 创建时使用更详细的序列化器
            return SpaceCreateUpdateSerializer
            # GET 请求使用列表序列化器
        return SpaceListSerializer

    def get_queryset(self):
        """
        根据用户角色获取可用的空间 QuerySet。
        Service 层 (SpaceService.get_all_spaces) 必须实现此权限逻辑。
        """
        user = self.request.user
        space_service = SpaceService()
        # SpaceService().get_all_spaces 内部会根据 user 自动过滤权限
        service_result = space_service.get_all_spaces(user)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        """
        列出所有可见空间，并根据用户角色进行缓存隔离。
        普通用户：is_basic_infrastructure 为 True 或 用户组在 permitted_groups 中的空间。
        系统/空间管理员：所有空间。
        """
        try:
            user = request.user
            space_service = SpaceService()
            cache_key_prefix = 'spaces:space'  # 基础缓存前缀

            # 1. 动态生成 user_specific_postfix 用于缓存隔离
            # 这个后缀包含了用户的角色信息，确保不同权限等级/不同用户的缓存互不干扰
            user_specific_postfix = ''
            if not user.is_authenticated:  # 理论上 IsAuthenticated 会阻止匿名用户，但作为防御性编程
                user_specific_postfix = 'anonymous'
            elif user.is_system_admin:
                user_specific_postfix = 'system_admin'
            elif user.is_space_manager:
                # 空间管理员可以看所有空间，如果不同空间管理员看到的结果相同，可以用 'space_manager'
                # 如果不同空间管理员看到的结果可能不同（例如基于他们管理的特定空间），需要加上 user.pk
                # 根据您提供的权限要求 "空间管理员也可以看到所有空间信息"，这里统一用 'space_manager'。
                # 如果 SpaceService.get_all_spaces 实际上会因空间管理员不同而返回不同结果，则改为 f'space_manager:{user.pk}'
                user_specific_postfix = 'space_manager'
            else:
                # 普通用户，其可见空间可能受限于其所属用户组，因此需要用户ID来隔离
                user_specific_postfix = f'normal_user:{user.pk}'

            # 结合基础列表类型和用户特定后缀
            final_custom_postfix = f"list_all_visible_by_user:{user_specific_postfix}"

            # 2. 生成查询参数的哈希作为缓存的一部分
            dynamic_cache_kwargs = space_service.get_dynamic_list_cache_key_parts(request.query_params)

            # 3. 尝试从缓存获取数据
            cached_full_response_data = CacheService.get_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=final_custom_postfix,  # 使用包含用户角色的自定义后缀
                **dynamic_cache_kwargs
            )

            if cached_full_response_data is not None:
                logger.debug(
                    f"View 层缓存命中空间列表数据 (User: {user.username}, Postfix: {final_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")
                return success_response(
                    message=MSG_SUCCESS,
                    data=cached_full_response_data,
                    status_code=HTTP_200_OK
                )

            # 4. 缓存未命中，从数据库获取数据并序列化
            queryset_unpaginated_filtered = self.filter_queryset(self.get_queryset())

            total_count = queryset_unpaginated_filtered.count()

            page_data = None
            if self.pagination_class:
                self.request.successful_response_status = HTTP_200_OK
                paginator = self.pagination_class()
                page_data = paginator.paginate_queryset(queryset_unpaginated_filtered, self.request, view=self)
            else:
                page_data = list(queryset_unpaginated_filtered)

            serializer = self.get_serializer(page_data, many=True,
                                             context={'request': request})  # 传递 request 到 serializer context
            final_serialised_results = serializer.data

            final_response_data_for_cache = None
            if self.pagination_class and paginator:
                final_response_data_for_cache = paginator.get_paginated_response(final_serialised_results).data
            else:
                final_response_data_for_cache = {
                    "count": total_count,
                    "next": None,
                    "previous": None,
                    "results": final_serialised_results
                }

            # 5. 设置缓存
            # 获取超时时间：使用基础列表类型 'spaces:space:list_all' 来查找 TIMEOUTS_MAP
            timeout = CacheService.get_timeout_for_key_prefix(f"{cache_key_prefix}:list_all")
            CacheService.set_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=final_custom_postfix,  # 使用包含用户角色的自定义后缀
                value=final_response_data_for_cache,
                timeout=timeout,
                **dynamic_cache_kwargs
            )
            logger.debug(
                f"View 层缓存空间列表数据 (User: {user.username}, Postfix: {final_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")

            return success_response(
                message=MSG_SUCCESS,
                data=final_response_data_for_cache,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in SpaceListCreateAPIView (list):   {e.detail}")
            raise e
        except Exception as e:
            logger.exception("列出空间失败，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_admin_or_space_manager_required  # 需要管理员或空间管理员权限来创建
    def create(self, request, *args, **kwargs):
        # ... (create 方法保持不变) ...
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            # 在创建成功后，清除所有相关列表缓存，确保不同用户都能刷新到最新数据
            CacheService.invalidate_all_related_cache('spaces:space')

            response_data = SpaceBaseSerializer(instance, context={'request': request}).data
            return success_response(
                message=MSG_CREATED,
                data=response_data,
                status_code=HTTP_201_CREATED
            )
        except CustomAPIException as e:
            logger.warning(f"Known API Exception caught in SpaceListCreateAPIView (create): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception("创建空间失败，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误。")


class SpaceRetrieveUpdateDestroyAPIView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            # 更新时使用更详细的序列化器
            return SpaceCreateUpdateSerializer
        # GET 请求使用基础序列化器
        return SpaceBaseSerializer

    def get_object(self):
        """
        根据用户角色获取单个空间对象。
        Service 层 (SpaceService.get_space_by_id) 必须实现此权限逻辑。
        """
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        space_service = SpaceService()

        # SpaceService().get_space_by_id 内部已通过 @CacheService.cache_method 实现了用户隔离的缓存
        service_result = space_service.get_space_by_id(user, pk)
        if service_result.success:
            # CachedDictObject 包装了字典数据，使其行为像一个模型实例
            return CachedDictObject(service_result.data, model_class=Space)
        else:
            # 如果 ServiceResult 是失败的，通常是 NotFoundException 或 ForbiddenException
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        """
        获取单个空间的详情。
        权限和缓存已在 get_object 和 Service 层处理。
        """
        try:
            instance = self.get_object()  # get_object() 已处理权限和缓存
            # 序列化实例，并传递 request context 以便 ImageField 生成完整的 URL
            serializer = self.get_serializer(instance, context={'request': request})
            return success_response(
                message=MSG_SUCCESS,
                data=serializer.data,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"获取空间详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_admin_or_space_manager_required  # 需要管理员或空间管理员权限来更新
    def update(self, request, *args, **kwargs):
        # ... (update 方法保持不变) ...
        # get_object() 上的装饰器已确保当前用户有权操作该空间
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            real_instance = Space.objects.get(pk=pk_from_url)
        except Space.DoesNotExist:
            raise NotFoundException(detail="空间未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            # 更新成功后，清除该空间的详情缓存和所有相关列表缓存
            CacheService.invalidate_object_cache('spaces:space', instance.pk)
            CacheService.invalidate_all_related_cache('spaces:space')

            response_data = SpaceBaseSerializer(instance, context={'request': request}).data
            return success_response(
                message="空间更新成功。",
                data=response_data,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (update): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"更新空间失败 (ID: {pk_from_url})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_admin_or_space_manager_required  # 需要管理员或空间管理员权限来删除
    def destroy(self, request, *args, **kwargs):
        # ... (destroy 方法保持不变) ...
        # get_object() 上的装饰器已确保当前用户有权操作该空间
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk
        user = request.user

        try:
            service_result = SpaceService().delete_space(user, pk_from_url)

            if service_result.success:
                # 删除成功后，清除所有相关列表缓存和该空间的详情缓存
                CacheService.invalidate_all_related_cache('spaces:space')

                return success_response(
                    message="空间删除成功。",
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()

        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (delete): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"删除空间失败 (ID: {pk_from_url})。")
            raise InternalServerError(detail="服务器内部错误。")


# --- NEW ADMIN INTERFACES: Managed Spaces (仅用于管理员视角的 GET 操作) ---

class ManagedSpaceListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = SpaceFilter

    def get_serializer_class(self):
        return SpaceListSerializer

    @is_admin_or_space_manager_for_qs_obj
    def get_queryset(self):
        user = self.request.user
        space_service = SpaceService()
        # 调用 Service 中获取管理空间的方法，DAO 层会根据用户角色过滤
        service_result = space_service.get_managed_spaces(user)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    @is_admin_or_space_manager_required  # 验证是管理员或空间管理器
    def list(self, request, *args, **kwargs):
        """
        列出当前用户管理的空间，或（对系统管理员）所有被管理的空间，并进行缓存隔离。
        """
        try:
            user = request.user
            space_service = SpaceService()
            cache_key_prefix = 'spaces:space'

            dynamic_cache_kwargs = space_service.get_dynamic_list_cache_key_parts(request.query_params)

            # 为管理空间列表生成特定于用户的缓存后缀
            # 这里的 fixed_custom_postfix 已经包含了用户身份信息，这是正确的
            user_specific_postfix = f"list_managed_by_user:{user.pk}"
            if user.is_system_admin:
                user_specific_postfix = "list_all_managed_by_admin"

            final_custom_postfix = user_specific_postfix  # 直接使用这个作为 custom_postfix

            cached_full_response_data = CacheService.get_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=final_custom_postfix,
                **dynamic_cache_kwargs
            )

            if cached_full_response_data is not None:
                logger.debug(
                    f"View 层缓存命中管理空间列表数据 (User: {user.username}, Postfix: {final_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")
                return success_response(
                    message=MSG_SUCCESS,
                    data=cached_full_response_data,
                    status_code=HTTP_200_OK
                )

            self.request.successful_response_status = HTTP_200_OK

            queryset_unpaginated_filtered = self.filter_queryset(self.get_queryset())

            total_count = queryset_unpaginated_filtered.count()

            page_data = None
            if self.pagination_class:
                paginator = self.pagination_class()
                page_data = paginator.paginate_queryset(queryset_unpaginated_filtered, self.request, view=self)
            else:
                page_data = list(queryset_unpaginated_filtered)

            serializer = self.get_serializer(page_data, many=True, context={'request': request})
            final_serialised_results = serializer.data

            final_response_data_for_cache = None
            if self.pagination_class and paginator:
                final_response_data_for_cache = paginator.get_paginated_response(final_serialised_results).data
            else:
                final_response_data_for_cache = {
                    "count": total_count,
                    "next": None,
                    "previous": None,
                    "results": final_serialised_results
                }

            # 获取超时时间：即使 custom_postfix 包含了 user.pk，我们查找超时时依然使用 'list_all'
            # 这样可以在 TIMEOUTS_MAP 中有一个统一的列表超时配置
            timeout = CacheService.get_timeout_for_key_prefix(f"{cache_key_prefix}:list_all")

            CacheService.set_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=final_custom_postfix,
                value=final_response_data_for_cache,
                timeout=timeout,
                **dynamic_cache_kwargs
            )
            logger.debug(
                f"View 层缓存管理空间列表数据 (User: {user.username}, Postfix: {final_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")

            return success_response(
                message=MSG_SUCCESS,
                data=final_response_data_for_cache,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in ManagedSpaceListCreateAPIView (list):  - {e.detail}")
            raise e
        except Exception as e:
            logger.exception("列出管理空间失败，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误。")

    # --- create 方法已删除。创建 Space 统一使用 /spaces/ 接口 ---
    def create(self, request, *args, **kwargs):
        raise InternalServerError(detail="此接口仅支持 GET 操作。请使用 /spaces/ 接口创建空间。")


class ManagedSpaceRetrieveUpdateDestroyAPIView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_serializer_class(self):
        return SpaceBaseSerializer

    @is_admin_or_space_manager_for_qs_obj
    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        space_service = SpaceService()
        # SpaceService().get_space_by_id 内部已通过 @CacheService.cache_method 实现了用户隔离的缓存
        service_result = space_service.get_space_by_id(user, pk)
        if service_result.success:
            space_data = service_result.data
            # 额外验证：确保是非系统管理员的空间管理员只能操作自己管理的空间
            if not user.is_system_admin and space_data.get('managed_by_id') != user.pk:
                raise ForbiddenException(f"您没有权限操纵非您管理的空间 (ID: {pk})。")
            return CachedDictObject(space_data, model_class=Space)
        else:
            raise service_result.to_exception()

    @is_admin_or_space_manager_required  # 验证是管理员或空间管理器
    def retrieve(self, request, *args, **kwargs):
        """获取单个管理空间详情。权限已在 get_object 和 Service 层处理。"""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, context={'request': request})
            return success_response(
                message=MSG_SUCCESS,
                data=serializer.data,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in ManagedSpaceRetrieveUpdateDestroyAPIView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"获取管理空间详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    # TBD: update 和 destroy 方法已删除。更新和删除 Space 统一使用 /spaces/<pk>/ 接口
    def update(self, request, *args, **kwargs):
        raise InternalServerError(detail="此接口仅支持 GET 操作。请使用 /spaces/{id}/ 接口更新空间。")

    def destroy(self, request, *args, **kwargs):
        raise InternalServerError(detail="此接口仅支持 GET 操作。请使用 /spaces/{id}/ 接口删除空间。")


# --- SpaceType API Views (保持不变) ---

class SpaceTypeListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return SpaceTypeCreateUpdateSerializer
        return SpaceTypeBaseSerializer

    def get_queryset(self):
        user = self.request.user
        space_type_service = SpaceTypeService()
        service_result = space_type_service.get_all_space_types(user)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            user = request.user
            cache_key_prefix = 'spaces:spacetype'
            fixed_custom_postfix = 'list_all'

            query_params_hash_kwargs = {}
            if request.query_params:
                query_params_hash_val = SpaceService()._get_request_query_params_hash(request.query_params)
                query_params_hash_kwargs = {'query_params_hash': query_params_hash_val}

            cached_data = CacheService.get_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                **query_params_hash_kwargs
            )
            if cached_data is not None:
                logger.debug(
                    f"View 层缓存命中空间类型列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {query_params_hash_kwargs.get('query_params_hash', 'N/A')}).")
                return success_response(
                    message=MSG_SUCCESS,
                    data={"results": cached_data['results'], "count": cached_data['count']},
                    status_code=HTTP_200_OK
                )

            queryset_filtered = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset_filtered, many=True, context={'request': request})
            response_data = serializer.data
            total_count = queryset_filtered.count()

            final_response_data_for_cache = {
                "results": response_data,
                "count": total_count
            }

            timeout = CacheService.get_timeout_for_key_prefix('spaces:spacetype:list_all')
            CacheService.set_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                value=final_response_data_for_cache,
                timeout=timeout,
                **query_params_hash_kwargs
            )
            logger.debug(
                f"View 层缓存空间类型列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {query_params_hash_kwargs.get('query_params_hash', 'N/A')}).")

            return success_response(
                message=MSG_SUCCESS,
                data={"results": response_data, "count": total_count},
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"Known API Exception caught in SpaceTypeListView (list): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception("列出空间类型失败，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            # 创建成功后清除相关缓存
            CacheService.invalidate_all_related_cache('spaces:spacetype')

            response_data = SpaceTypeBaseSerializer(instance, context={'request': request}).data
            return success_response(
                message=MSG_CREATED,
                data=response_data,
                status_code=HTTP_201_CREATED
            )
        except CustomAPIException as e:
            logger.warning(f"Known API Exception caught in SpaceTypeListView (create): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception("创建空间类型失败，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误。")


class SpaceTypeDetailUpdateDestroyView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return SpaceTypeCreateUpdateSerializer
        return SpaceTypeBaseSerializer

    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        space_type_service = SpaceTypeService()
        # 这里 SpaceTypeService().get_space_type_by_id 也应加上用户参数以支持将来可能的权限控制
        service_result = space_type_service.get_space_type_by_id(user, pk)
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=SpaceType)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, context={'request': request})
            return success_response(
                message=MSG_SUCCESS,
                data=serializer.data,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceTypeDetailUpdateDestroyView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"获取空间类型详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def update(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            service_get_result = SpaceTypeService().get_space_type_by_id(self.request.user, pk_from_url)
            if not service_get_result.success:
                raise service_get_result.to_exception()
            real_instance = SpaceType.objects.get(pk=pk_from_url)
        except SpaceType.DoesNotExist:
            raise NotFoundException(detail="空间类型未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            # 更新成功后清除缓存
            CacheService.invalidate_object_cache('spaces:spacetype', instance.pk)
            CacheService.invalidate_all_related_cache('spaces:spacetype')

            response_data = SpaceTypeBaseSerializer(instance, context={'request': request}).data
            return success_response(
                message="空间类型更新成功。",
                data=response_data,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceTypeDetailUpdateDestroyView (update): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"更新空间类型失败 (ID: {pk_from_url})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def destroy(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        user = request.user

        try:
            service_result = SpaceTypeService().delete_space_type(user, pk_from_url)

            if service_result.success:
                # 删除成功后清除缓存
                CacheService.invalidate_all_related_cache('spaces:spacetype')
                return success_response(
                    message="空间类型删除成功。",
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()

        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceTypeDetailUpdateDestroyView (delete): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"删除空间类型失败 (ID: {pk_from_url})。")
            raise InternalServerError(detail="服务器内部错误。")


# --- Amenity API Views (保持不变) ---

class AmenityListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AmenityCreateUpdateSerializer
        return AmenityBaseSerializer

    def get_queryset(self):
        user = self.request.user
        amenity_service = AmenityService()
        # 这里 AmenityService().get_all_amenities 也应加上用户参数以支持将来可能的权限控制
        service_result = amenity_service.get_all_amenities(user)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            user = request.user
            cache_key_prefix = 'spaces:amenity'
            fixed_custom_postfix = 'list_all'

            query_params_hash_kwargs = {}
            if request.query_params:
                query_params_hash_val = SpaceService()._get_request_query_params_hash(request.query_params)
                query_params_hash_kwargs = {'query_params_hash': query_params_hash_val}

            cached_data = CacheService.get_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                **query_params_hash_kwargs
            )
            if cached_data is not None:
                logger.debug(
                    f"View 层缓存命中设施类型列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {query_params_hash_kwargs.get('query_params_hash', 'N/A')}).")
                return success_response(
                    message=MSG_SUCCESS,
                    data={"results": cached_data['results'], "count": cached_data['count']},
                    status_code=HTTP_200_OK
                )

            queryset_filtered = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset_filtered, many=True, context={'request': request})
            response_data = serializer.data
            total_count = queryset_filtered.count()

            final_response_data_for_cache = {
                "results": response_data,
                "count": total_count
            }

            timeout = CacheService.get_timeout_for_key_prefix('spaces:amenity:list_all')
            CacheService.set_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                value=final_response_data_for_cache,
                timeout=timeout,
                **query_params_hash_kwargs
            )
            logger.debug(
                f"View 层缓存设施类型列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {query_params_hash_kwargs.get('query_params_hash', 'N/A')}).")

            return success_response(
                message=MSG_SUCCESS,
                data={"results": response_data, "count": total_count},
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"Known API Exception caught in AmenityListView (list): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception("列出设施类型失败，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            CacheService.invalidate_all_related_cache('spaces:amenity')
            response_data = AmenityBaseSerializer(instance, context={'request': request}).data
            return success_response(
                message=MSG_CREATED,
                data=response_data,
                status_code=HTTP_201_CREATED
            )
        except CustomAPIException as e:
            logger.warning(f"Known API Exception caught in AmenityListView (create): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception("创建设施类型失败，发生未知错误。")
            raise InternalServerError(detail=str(e))


class AmenityDetailUpdateDestroyView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return AmenityCreateUpdateSerializer
        return AmenityBaseSerializer

    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        amenity_service = AmenityService()
        # 这里 AmenityService().get_amenity_by_id 也应加上用户参数以支持将来可能的权限控制
        service_result = amenity_service.get_amenity_by_id(user, pk)
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=Amenity)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, context={'request': request})
            return success_response(
                message=MSG_SUCCESS,
                data=serializer.data,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in AmenityDetailUpdateDestroyView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"获取设施类型详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def update(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            service_get_result = AmenityService().get_amenity_by_id(self.request.user, pk_from_url)
            if not service_get_result.success:
                raise service_get_result.to_exception()
            real_instance = Amenity.objects.get(pk=pk_from_url)
        except Amenity.DoesNotExist:
            raise NotFoundException(detail="设施类型未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            CacheService.invalidate_object_cache('spaces:amenity', instance.pk)
            CacheService.invalidate_all_related_cache('spaces:amenity')
            response_data = AmenityBaseSerializer(instance, context={'request': request}).data
            return success_response(
                message="设施类型更新成功。",
                data=response_data,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in AmenityDetailUpdateDestroyView (update): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"更新设施类型失败 (ID: {pk_from_url})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def destroy(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        user = request.user

        try:
            service_result = AmenityService().delete_amenity(user, pk_from_url)

            if service_result.success:
                CacheService.invalidate_all_related_cache('spaces:amenity')
                return success_response(
                    message="设施类型删除成功。",
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()

        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in AmenityDetailUpdateDestroyView (delete): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"删除设施类型失败 (ID: {pk_from_url})。")
            raise InternalServerError(detail=f"服务器内部错误: {str(e)}")