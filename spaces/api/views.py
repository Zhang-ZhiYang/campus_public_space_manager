# spaces/api/views.py
import hashlib  # For hashing query parameters for cache key
import json  # For serializing query parameters for hashing
from typing import Dict, Any

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from core.pagination import CustomPageNumberPagination

import logging

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError, NotFoundException

from core.utils.constants import MSG_CREATED, MSG_SUCCESS, HTTP_201_CREATED, HTTP_200_OK, HTTP_204_NO_CONTENT
from spaces.api.filters import SpaceFilter  # Ensure SpaceFilter exists or replace if not needed
from core.cache import CacheService  # Import the actual CacheService from core.cache

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


# --- Helper for get_object when Service returns dict from cache ---
class CachedDictObject:
    """
    A simple wrapper for dictionary data that mimics a Django model instance
    enough for DRF's `get_object()` to work, specifically by providing a `pk` attribute.
    It also stores the original `_model_class` for resolution in update/delete.
    """

    def __init__(self, data: Dict[str, Any], model_class=None):
        self._data = data
        self._model_class = model_class

    def __getattr__(self, name):
        """Allow direct access to dict keys as attributes. Modified to handle nested dicts (related objects)."""
        if name in self._data:
            value = self._data[name]
            # Recursively wrap nested dictionaries for related fields if they are also expected as objects
            if isinstance(value, dict) and name in ['space_type', 'managed_by',
                                                    'parent_space']:  # Add other related fields if needed
                return CachedDictObject(value)
            return value
        elif name == 'pk' and 'id' in self._data:  # For `instance.pk` lookup
            return self._data['id']
        elif name == 'permitted_groups':  # For obj.permitted_groups.exists() or iteration
            # Return a list (or empty list) of PKs. The serializer should handle this for display.
            # In cached dict, permitted_groups will be a list of PKs.
            return self._data.get('permitted_groups', [])
        # For non-existent attributes, raise AttributeError
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

        # Required for some DRF validation/lookup to work with instance context

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.pk == other.pk
        if self._model_class and isinstance(other, self._model_class):  # Compare with actual model instance
            return self.pk == other.pk
        if hasattr(other, 'pk') and self.pk == other.pk:  # For comparison with other DRF objects
            return True
        if isinstance(other, dict) and 'id' in other:  # Compare with dict if needed
            return self.pk == other['id']
        return NotImplemented

    def __hash__(self):
        return hash(self.pk)


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
        user = self.request.user
        space_service = SpaceService()

        service_result = space_service.get_all_spaces(user)
        if service_result.success:
            return service_result.data  # This is a QuerySet
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            user = request.user
            # === Part 1: Prepare Cache Key ===
            cache_key_prefix = 'spaces:space:list_all'

            # Combine user ID and all query parameters to create a unique hash for the cache key
            dynamic_parts = {
                'user_id': user.pk,
                **request.query_params  # Include all query params (filters, pagination, etc.)
            }
            # Convert to a stable string representation for hashing
            sorted_dynamic_parts = dict(sorted(dynamic_parts.items()))
            dynamic_key_string = json.dumps(sorted_dynamic_parts, sort_keys=True)
            dynamic_key_hash = hashlib.md5(dynamic_key_string.encode('utf-8')).hexdigest()

            # Use 'filtered_paginated_results' as a constant custom_postfix,
            # and rely on dynamic_key_hash for unique variations
            list_cache_custom_postfix = 'filtered_paginated_results'

            # === Part 2: Try to get data from cache ===
            # The `CacheService.get_list_cache` will use the `dynamic_hash` in its kwargs to build the key.
            cached_full_response_data = CacheService.get_list_cache(
                cache_key_prefix, list_cache_custom_postfix, dynamic_hash=dynamic_key_hash
            )

            if cached_full_response_data is not None:
                logger.debug(f"View 层缓存命中空间列表数据 (User: {user.username}, PostfixHash: {dynamic_key_hash}).")
                # If cached_full_response_data is a dict containing 'count', 'next', 'previous', 'results'
                # it's already in the format we need to return.
                return success_response(
                    message=MSG_SUCCESS,
                    data=cached_full_response_data,
                    status_code=HTTP_200_OK
                )

            # === Part 3: Cache Miss - Get data from database, serialize, and then set cache ===
            queryset_unpaginated_filtered = self.filter_queryset(self.get_queryset())  # Apply filters to QuerySet

            total_count = queryset_unpaginated_filtered.count()

            page_data = None
            final_serialised_results = []

            if self.pagination_class:
                self.request.successful_response_status = HTTP_200_OK
                paginator = self.pagination_class()
                page_data = paginator.paginate_queryset(queryset_unpaginated_filtered, self.request, view=self)
            else:
                # If no pagination, simply convert the filtered queryset to a list
                page_data = list(queryset_unpaginated_filtered)

            serializer = self.get_serializer(page_data, many=True)
            final_serialised_results = serializer.data  # This is List[Dict] (current page's results)

            # Construct the final response data structure, including pagination info if applicable
            final_response_data_for_cache = None
            if self.pagination_class and paginator:
                # Use the paginator's method to get the full paginated response structure
                final_response_data_for_cache = paginator.get_paginated_response(final_serialised_results).data
            else:
                # For non-paginated lists, manually build the response structure
                final_response_data_for_cache = {
                    "count": total_count,
                    "next": None,
                    "previous": None,
                    "results": final_serialised_results
                }

            # === Part 4: Set the new data into cache ===
            timeout = CacheService.get_timeout_for_key_prefix(cache_key_prefix)
            CacheService.set_list_cache(
                cache_key_prefix, list_cache_custom_postfix, final_response_data_for_cache,
                timeout, dynamic_hash=dynamic_key_hash
            )
            logger.debug(f"View 层缓存空间列表数据 (User: {user.username}, PostfixHash: {dynamic_key_hash}).")

            # === Part 5: Return the response ===
            return success_response(
                message=MSG_SUCCESS,
                data=final_response_data_for_cache,  # return the prepared data
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
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service_result = SpaceService().get_space_by_id(user, pk)
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=Space)
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
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"获取空间详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_admin_or_space_manager_required
    def update(self, request, *args, **kwargs):
        # The `self.get_object()` here returns a CachedDictObject, solely for permissions and quick PK access.
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            # For `serializer.update` and direct DB operations, we need the REAL model instance.
            # First, verify user has permission to view/update via service layer.
            service_get_result = SpaceService().get_space_by_id(self.request.user, pk_from_url)
            if not service_get_result.success:
                raise service_get_result.to_exception()

            # Then, fetch the actual Django model instance.
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
        # The `self.get_object()` here returns a CachedDictObject, solely for permissions and quick PK access.
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        user = request.user

        try:
            # We don't need `real_instance` for service.delete in the same way as serializer.update,
            # as service.delete directly takes PK
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
    pagination_class = None  # No pagination for SpaceType list

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return SpaceTypeCreateUpdateSerializer
        return SpaceTypeBaseSerializer

    def get_queryset(self):
        user = self.request.user
        space_type_service = SpaceTypeService()
        service_result = space_type_service.get_all_space_types(user)
        if service_result.success:
            return service_result.data  # This is a QuerySet
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            user = request.user
            # === Part 1: Prepare Cache Key ===
            cache_key_prefix = 'spaces:spacetype:list_all'
            cache_postfix = 'list_all'  # Static for this general list. Could also include `user.pk` for user-specific caching.

            # === Part 2: Try to get data from cache ===
            cached_data = CacheService.get_list_cache(cache_key_prefix, cache_postfix)
            if cached_data is not None:
                logger.debug(f"View 层缓存命中空间类型列表数据 (User: {user.username}, Postfix: {cache_postfix}).")
                # Since pagination_class is None, the cached_data is already a list of dicts.
                return success_response(
                    message=MSG_SUCCESS,
                    data={"results": cached_data, "count": len(cached_data)},
                    status_code=HTTP_200_OK
                )

            # === Part 3: Cache Miss - Get data from database, serialize, and then set cache ===
            queryset_filtered = self.filter_queryset(self.get_queryset())  # Apply filters to QuerySet
            serializer = self.get_serializer(queryset_filtered, many=True)
            response_data = serializer.data  # This is List[Dict]

            # === Part 4: Set the new data into cache ===
            timeout = CacheService.get_timeout_for_key_prefix(cache_key_prefix)
            CacheService.set_list_cache(cache_key_prefix, cache_postfix, response_data, timeout)
            logger.debug(f"View 层缓存空间类型列表数据 (User: {user.username}, Postfix: {cache_postfix}).")

            # === Part 5: Return the response ===
            return success_response(
                message=MSG_SUCCESS,
                data={"results": response_data, "count": queryset_filtered.count()},
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


# --- Amenity API Views ---

class AmenityListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = None  # No pagination for Amenity list

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AmenityCreateUpdateSerializer
        return AmenityBaseSerializer

    def get_queryset(self):
        user = self.request.user
        amenity_service = AmenityService()
        service_result = amenity_service.get_all_amenities(user)
        if service_result.success:
            return service_result.data  # This is a QuerySet
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            user = request.user
            # === Part 1: Prepare Cache Key ===
            cache_key_prefix = 'spaces:amenity:list_all'
            cache_postfix = 'list_all'  # Static for this general list. Could also include `user.pk` for user-specific caching.

            # === Part 2: Try to get data from cache ===
            cached_data = CacheService.get_list_cache(cache_key_prefix, cache_postfix)
            if cached_data is not None:
                logger.debug(f"View 层缓存命中设施类型列表数据 (User: {user.username}, Postfix: {cache_postfix}).")
                # Since pagination_class is None, the cached_data is already a list of dicts.
                return success_response(
                    message=MSG_SUCCESS,
                    data={"results": cached_data, "count": len(cached_data)},
                    status_code=HTTP_200_OK
                )

            # === Part 3: Cache Miss - Get data from database, serialize, and then set cache ===
            queryset_filtered = self.filter_queryset(self.get_queryset())  # Apply filters to QuerySet
            serializer = self.get_serializer(queryset_filtered, many=True)
            response_data = serializer.data  # This is List[Dict]

            # === Part 4: Set the new data into cache ===
            timeout = CacheService.get_timeout_for_key_prefix(cache_key_prefix)
            CacheService.set_list_cache(cache_key_prefix, cache_postfix, response_data, timeout)
            logger.debug(f"View 层缓存设施类型列表数据 (User: {user.username}, Postfix: {cache_postfix}).")

            # === Part 5: Return the response ===
            return success_response(
                message=MSG_SUCCESS,
                data={"results": response_data, "count": queryset_filtered.count()},
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