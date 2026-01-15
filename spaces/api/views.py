# spaces/api/views.py

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from core.pagination import CustomPageNumberPagination

import logging

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError, NotFoundException

from core.utils.constants import MSG_CREATED, MSG_SUCCESS, HTTP_201_CREATED, HTTP_200_OK, HTTP_204_NO_CONTENT
from spaces.api.filters import SpaceFilter  # Assume SpaceFilter exists or replace if not needed
from core.service.cache import CacheService, CachedDictObject  # Import CachedDictObject from core.cache

from spaces.service.space_service import SpaceService
from spaces.service.space_type_service import SpaceTypeService
from spaces.service.amenity_service import AmenityService

from spaces.api.serializers import (
    SpaceListSerializer, SpaceCreateUpdateSerializer, SpaceBaseSerializer,
    AmenityBaseSerializer, AmenityCreateUpdateSerializer,
    SpaceTypeBaseSerializer, SpaceTypeCreateUpdateSerializer
)

from core.decorators import is_system_admin_required, is_admin_or_space_manager_required
from spaces.models import Amenity, Space, SpaceType  # Used for get_object/pk error handling

logger = logging.getLogger(__name__)


# Note: CachedDictObject is now defined in core.cache and imported.
# Its definition should be updated there to handle M2M field serialization.

# --- Space API Views ---

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
        """
        Service层仍然返回QuerySet，便于FilterBackend和Pagination处理，
        权限过滤在Service层处理。
        """
        user = self.request.user
        space_service = SpaceService()

        service_result = space_service.get_all_spaces(user)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        """
        列出所有空间，包含缓存逻辑。
        公共列表不再根据用户角色生成不同的缓存键，仅根据查询参数哈希区分。
        """
        try:
            user = request.user
            space_service = SpaceService()
            cache_key_prefix = 'spaces:space'

            # === Part 1: Prepare Cache Key Components (Only Query Params Hash now) ===
            # `get_dynamic_list_cache_key_parts` 现在只返回 {'query_params_hash': 'abcde'}
            dynamic_cache_kwargs = space_service.get_dynamic_list_cache_key_parts(request.query_params)

            # 使用 'list_all' 作为列表的固定 custom_postfix
            fixed_custom_postfix = 'list_all'

            # === Part 2: Try to get data from cache ===
            cached_full_response_data = CacheService.get_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                **dynamic_cache_kwargs  # 直接传递包含查询参数哈希的字典
            )

            if cached_full_response_data is not None:
                logger.debug(
                    f"View 层缓存命中空间列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")
                return success_response(
                    message=MSG_SUCCESS,
                    data=cached_full_response_data,
                    status_code=HTTP_200_OK
                )

            # === Part 3: Cache Miss - Get data from database, serialize, and then set cache ===
            # 应用过滤器到 QuerySet (DjangoFilterBackend)
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
                # 使用分页器的方法获取完整的带分页信息的响应结构
                final_response_data_for_cache = paginator.get_paginated_response(final_serialised_results).data
            else:
                # 对于无分页的列表，手动构建响应结构
                final_response_data_for_cache = {
                    "count": total_count,
                    "next": None,
                    "previous": None,
                    "results": final_serialised_results
                }

            # === Part 4: Set the new data into cache ===
            # 从 TIMEOUTS_MAP 获取过期时间（例如 'spaces:space:list_all'）
            timeout = CacheService.get_timeout_for_key_prefix('spaces:space:list_all')
            CacheService.set_list_cache(
                key_prefix=cache_key_prefix,
                custom_postfix=fixed_custom_postfix,
                value=final_response_data_for_cache,
                timeout=timeout,
                **dynamic_cache_kwargs  # 再次传递查询参数哈希
            )
            logger.debug(
                f"View 层缓存空间列表数据 (User: {user.username}, Postfix: {fixed_custom_postfix}, QPHash: {dynamic_cache_kwargs.get('query_params_hash', 'N/A')}).")

            # === Part 5: Return the response ===
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

    @is_admin_or_space_manager_required
    def create(self, request, *args, **kwargs):
        """创建新的空间。"""
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

    def get_object(self):
        """
        获取单个空间对象。Service层通过装饰器处理缓存，这里获取的是缓存或DB的ServiceResult数据。
        然后包装为 `CachedDictObject` 供 DRF 使用。
        """
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service_result = SpaceService().get_space_by_id(user, pk)
        if service_result.success:
            # CachedDictObject 已经修复，可以正确处理 M2M 字段
            return CachedDictObject(service_result.data, model_class=Space)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        """获取单个空间详情。"""
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
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"获取空间详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_admin_or_space_manager_required
    def update(self, request, *args, **kwargs):
        """更新空间。"""
        # `get_object()` 返回 CachedDictObject，用于快速获取PK或进行权限检查，
        # 但更新操作需要真实的模型实例。
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            # First, verify user has permission to view/update via service layer.
            service_get_result = SpaceService().get_space_by_id(self.request.user, pk_from_url)
            if not service_get_result.success:
                raise service_get_result.to_exception()

            # Then, fetch the actual Django model instance for the serializer.
            real_instance = Space.objects.get(pk=pk_from_url)
        except Space.DoesNotExist:
            raise NotFoundException(detail="空间未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()  # This calls serializer.update(), which then calls SpaceService().update_space
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

    @is_admin_or_space_manager_required
    def destroy(self, request, *args, **kwargs):
        """删除空间。"""
        # `get_object()` 返回 CachedDictObject，用于快速获取PK或进行权限检查，
        # 删除操作直接通过 Service.delete_space() 处理
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


# --- SpaceType API Views ---

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
        """列出所有空间类型，包含缓存逻辑。"""
        try:
            user = request.user
            cache_key_prefix = 'spaces:spacetype'
            fixed_custom_postfix = 'list_all'

            query_params_hash_kwargs = {}
            if request.query_params:
                # 如果有查询参数，哈希它们作为缓存键的一部分
                query_params_hash_val = SpaceService()._get_request_query_params_hash(request.query_params)
                query_params_hash_kwargs = {'query_params_hash': query_params_hash_val}

            # === Part 2: Try to get data from cache ===
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

            # === Part 3: Cache Miss - Get data from database, serialize, and then set cache ===
            queryset_filtered = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset_filtered, many=True)
            response_data = serializer.data
            total_count = queryset_filtered.count()

            final_response_data_for_cache = {
                "results": response_data,
                "count": total_count
            }

            # === Part 4: Set the new data into cache ===
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

            # === Part 5: Return the response ===
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
        """创建新的空间类型。"""
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
        """获取单个空间类型对象。Service层通过装饰器处理缓存。"""
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service_result = SpaceTypeService().get_space_type_by_id(user, pk)
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=SpaceType)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        """获取单个空间类型详情。"""
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
        """更新空间类型。"""
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
        """删除空间类型。"""
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


# --- Amenity API Views ---

class AmenityListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AmenityCreateUpdateSerializer
        return AmenityBaseSerializer

    def get_queryset(self):
        """
        Service层仍然返回QuerySet，便于FilterBackend和Pagination处理。
        """
        user = self.request.user
        amenity_service = AmenityService()
        service_result = amenity_service.get_all_amenities(user)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        """列出所有设施类型，包含缓存逻辑。"""
        try:
            user = request.user
            cache_key_prefix = 'spaces:amenity'
            fixed_custom_postfix = 'list_all'

            query_params_hash_kwargs = {}
            if request.query_params:
                query_params_hash_val = SpaceService()._get_request_query_params_hash(request.query_params)
                query_params_hash_kwargs = {'query_params_hash': query_params_hash_val}

            # === Part 2: Try to get data from cache ===
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

            # === Part 3: Cache Miss - Get data from database, serialize, and then set cache ===
            queryset_filtered = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset_filtered, many=True)
            response_data = serializer.data
            total_count = queryset_filtered.count()

            final_response_data_for_cache = {
                "results": response_data,
                "count": total_count
            }

            # === Part 4: Set the new data into cache ===
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

            # === Part 5: Return the response ===
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
        """创建新的设施类型。"""
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
        """获取单个设施类型对象。Service层通过装饰器处理缓存。"""
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service_result = AmenityService().get_amenity_by_id(user, pk)
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=Amenity)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        """获取单个设施类型详情。"""
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
        """更新设施类型。"""
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
        """删除设施类型。"""
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