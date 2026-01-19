# spaces/api/views.py

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from core.pagination import CustomPageNumberPagination

import logging

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError, NotFoundException, \
    ForbiddenException  # Added ForbiddenException
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

from core.decorators import is_system_admin_required, is_admin_or_space_manager_required
from spaces.models import Amenity, Space, SpaceType

logger = logging.getLogger(__name__)


# ( _get_request_query_params_hash 函数保持不变，如果它还在这个文件里)

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
        # DAO now handles user-based filtering for get_all_spaces
        service_result = space_service.get_all_spaces(user)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        """
        列出所有空间，包含缓存逻辑。
        """
        try:
            user = request.user
            space_service = SpaceService()
            cache_key_prefix = 'spaces:space'

            dynamic_cache_kwargs = space_service.get_dynamic_list_cache_key_parts(request.query_params)
            # 添加用户角色信息到缓存键，确保不同用户看到的列表可以独立缓存
            # 例如 'list_all:user_pk_1', 'list_all:admin'
            user_specific_postfix = f"list_all_by_user:{user.pk}"  # More precise
            if user.is_system_admin:
                user_specific_postfix = "list_all_by_admin"
            elif user.is_space_manager:
                user_specific_postfix = f"list_all_by_spacemanager:{user.pk}"

            # 使用列表的 custom_postfix
            fixed_custom_postfix = user_specific_postfix

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

            timeout = CacheService.get_timeout_for_key_prefix(
                f"{cache_key_prefix}:{fixed_custom_postfix.split(':')[0]}")  # Use base postfix for timeout lookup
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

    @is_admin_or_space_manager_required  # Only admin/space_manager can create spaces
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
        service_result = SpaceService().get_space_by_id(user, pk)  # Service already includes permission check
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=Space)
        else:
            raise service_result.to_exception()

    # Retrieve permissions are handled by get_object/SpaceService.get_space_by_id
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

    @is_admin_or_space_manager_required  # Only admin/space_manager can update
    def update(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()  # This will ensure user has retrieve permission
        pk_from_url = _instance_for_pk.pk

        try:
            # Need actual Django model instance for serializer.save()
            real_instance = Space.objects.get(pk=pk_from_url)
        except Space.DoesNotExist:
            raise NotFoundException(detail="空间未找到。")  # Should ideally be caught by get_object

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})  # Pass context
        serializer.is_valid(raise_exception=True)

        try:
            # Service.update_space will handle detailed permission checks for fields
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

    @is_admin_or_space_manager_required  # Only admin/space_manager can delete
    def destroy(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()  # This will ensure user has retrieve permission
        pk_from_url = _instance_for_pk.pk
        user = request.user

        try:
            # Service.delete_space will handle detailed permission checks
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


# --- NEW: Space Manager's dedicated view for *their* managed spaces ---
class ManagedSpaceListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = SpaceFilter  # Can still apply filters to managed spaces

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return SpaceCreateUpdateSerializer
        return SpaceListSerializer

    @is_admin_or_space_manager_required  # Only space manager or system admin can access this view
    def get_queryset(self):
        user = self.request.user
        space_service = SpaceService()
        # Call the new service method to get only managed spaces
        service_result = space_service.get_managed_spaces(user)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    @is_admin_or_space_manager_required  # Manager or admin can create, following service logic for restrictions
    def create(self, request, *args, **kwargs):
        # Delegate to the main SpaceListCreateAPIView's create logic
        # This will reuse serializer validation and service call, with the SpaceService()
        # already having the logic to ensure a space manager can only create/manage their own spaces.
        return SpaceListCreateAPIView.as_view()(request, *args, **kwargs)  # Re-use the existing create logic


class ManagedSpaceRetrieveUpdateDestroyAPIView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return SpaceCreateUpdateSerializer
        return SpaceBaseSerializer

    @is_admin_or_space_manager_required  # Only space manager or system admin can access this view
    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        # Use SpaceService().get_space_by_id which already has the permission logic
        service_result = SpaceService().get_space_by_id(user, pk)
        if service_result.success:
            # Additionally verify it's *their* managed space
            if service_result.data.get('managed_by_id') != user.pk and not user.is_system_admin:
                raise ForbiddenException(f"您没有权限操纵非您管理的空间 (ID: {pk})。")
            return CachedDictObject(service_result.data, model_class=Space)
        else:
            raise service_result.to_exception()

    # Retrieve, update, destroy methods on this view will use the decorated get_object
    # and then delegate to the main SpaceRetrieveUpdateDestroyAPIView's methods,
    # or implement their own logic, always calling the service with `user` for checks.

    @is_admin_or_space_manager_required
    def retrieve(self, request, *args, **kwargs):
        # We can directly reuse the parent's implementation
        return super().retrieve(request, *args, **kwargs)

    @is_admin_or_space_manager_required
    def update(self, request, *args, **kwargs):
        # Reuse main Space update logic, which also has service-level permission checks
        return SpaceRetrieveUpdateDestroyAPIView.as_view()(request, *args, **kwargs)

    @is_admin_or_space_manager_required
    def destroy(self, request, *args, **kwargs):
        # Reuse main Space delete logic, which also has service-level permission checks
        return SpaceRetrieveUpdateDestroyAPIView.as_view()(request, *args, **kwargs)
