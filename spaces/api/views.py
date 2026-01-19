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
            return SpaceCreateUpdateSerializer
        return SpaceListSerializer

    def get_queryset(self):
        user = self.request.user
        space_service = SpaceService()
        service_result = space_service.get_all_spaces(user)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            user = request.user
            space_service = SpaceService()
            cache_key_prefix = 'spaces:space'

            dynamic_cache_kwargs = space_service.get_dynamic_list_cache_key_parts(request.query_params)
            fixed_custom_postfix = 'list_all'

            cached_full_response_data = CacheService.get_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                **dynamic_cache_kwargs
            )

            if cached_full_response_data is not None:
                logger.debug(
                    f"View 层缓存命中空间列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")
                return success_response(
                    message=MSG_SUCCESS,
                    data=cached_full_response_data,
                    status_code=HTTP_200_OK
                )

            queryset_unpaginated_filtered = self.filter_queryset(self.get_queryset())

            total_count = queryset_unpaginated_filtered.count()

            page_data = None
            if self.pagination_class:
                self.request.successful_response_status = HTTP_200_OK
                paginator = self.pagination_class()
                page_data = paginator.paginate_queryset(queryset_unpaginated_filtered, self.request, view=self)
            else:
                page_data = list(queryset_unpaginated_filtered)

            serializer = self.get_serializer(page_data, many=True)
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

            timeout = CacheService.get_timeout_for_key_prefix('spaces:space:list_all')
            CacheService.set_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                value=final_response_data_for_cache,
                timeout=timeout,
                **dynamic_cache_kwargs
            )
            logger.debug(
                f"View 层缓存空间列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")

            return success_response(
                message=MSG_SUCCESS,
                data=final_response_data_for_cache,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in SpaceListCreateAPIView (list): {e.code} - {e.detail}")
            raise e
        except Exception as e:
            logger.exception("列出空间失败，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_admin_or_space_manager_required  # 需要管理员或空间管理员权限来创建
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            response_data = SpaceBaseSerializer(instance).data
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
            return SpaceCreateUpdateSerializer
        return SpaceBaseSerializer

    # --- 修正点：新增此装饰器，对 admin/manager 检查权限以获取单个空间详情 ---
    @is_admin_or_space_manager_for_qs_obj
    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        # SpaceService().get_space_by_id 内部已经包含了权限过滤
        service_result = SpaceService().get_space_by_id(user, pk)
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=Space)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()  # get_object() 上的装饰器会处理权限
            serializer = self.get_serializer(instance)
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
            response_data = SpaceBaseSerializer(instance).data
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
        # get_object() 上的装饰器已确保当前用户有权操作该空间
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk
        user = request.user

        try:
            service_result = SpaceService().delete_space(user, pk_from_url)

            if service_result.success:
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
        # 此视图的 POST 已经被移除，所以通常这个方法不会被调用，
        # 但为了避免潜在错误，可以返回 SpaceListSerializer 或 SpaceBaseSerializer
        return SpaceListSerializer

    @is_admin_or_space_manager_for_qs_obj
    def get_queryset(self):
        user = self.request.user
        space_service = SpaceService()
        service_result = space_service.get_managed_spaces(user)  # 调用 Service 中获取管理空间的方法
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    @is_admin_or_space_manager_required  # 验证是管理员或空间管理器
    def list(self, request, *args, **kwargs):
        """
        列出当前用户管理的空间，或（对系统管理员）所有被管理的空间。
        """
        try:
            user = request.user
            space_service = SpaceService()
            cache_key_prefix = 'spaces:space'

            dynamic_cache_kwargs = space_service.get_dynamic_list_cache_key_parts(request.query_params)

            # 为管理空间列表生成特定于用户的缓存后缀
            user_specific_postfix = f"list_managed_by_user:{user.pk}"
            if user.is_system_admin:
                user_specific_postfix = "list_all_managed_by_admin"

            fixed_custom_postfix = user_specific_postfix

            cached_full_response_data = CacheService.get_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                **dynamic_cache_kwargs
            )

            if cached_full_response_data is not None:
                logger.debug(
                    f"View 层缓存命中管理空间列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")
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

            serializer = self.get_serializer(page_data, many=True)
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

            timeout = CacheService.get_timeout_for_key_prefix('spaces:space:list_all')
            CacheService.set_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                value=final_response_data_for_cache,
                timeout=timeout,
                **dynamic_cache_kwargs
            )
            logger.debug(
                f"View 层缓存管理空间列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")

            return success_response(
                message=MSG_SUCCESS,
                data=final_response_data_for_cache,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in ManagedSpaceListCreateAPIView (list): {e.code} - {e.detail}")
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
        # 此视图的 PUT/PATCH/DELETE 已经被移除，所以通常这个方法不会被调用，
        # 仅为 retrieve 返回 SpaceBaseSerializer 即可
        return SpaceBaseSerializer

    @is_admin_or_space_manager_for_qs_obj
    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service_result = SpaceService().get_space_by_id(user, pk)
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
        """获取单个管理空间详情。权限已在 get_object 中处理。"""
        # 父类的 retrieve 方法会调用 get_object()
        return super().retrieve(request, *args, **kwargs)

    # --- update 和 destroy 方法已删除。更新和删除 Space 统一使用 /spaces/<pk>/ 接口 ---
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
            serializer = self.get_serializer(queryset_filtered, many=True)
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
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            response_data = SpaceTypeBaseSerializer(instance).data
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
        service_result = SpaceTypeService().get_space_type_by_id(user, pk)
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=SpaceType)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
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
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            response_data = SpaceTypeBaseSerializer(instance).data
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
            serializer = self.get_serializer(queryset_filtered, many=True)
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
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            response_data = AmenityBaseSerializer(instance).data
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
        service_result = AmenityService().get_amenity_by_id(user, pk)
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=Amenity)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
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
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            response_data = AmenityBaseSerializer(instance).data
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