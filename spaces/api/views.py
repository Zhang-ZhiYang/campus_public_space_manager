# spaces/api/views.py
from rest_framework.views import APIView
from rest_framework.response import Response as DRFResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView, ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination

import logging

from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework.exceptions import ValidationError as DRFValidationError, NotFound as DRFNotFound, \
    PermissionDenied as DRFPermissionDenied, AuthenticationFailed, NotAuthenticated

from core.utils.response import success_response, error_response
from core.utils.exceptions import CustomAPIException, ServiceException, BadRequestException, NotFoundException, \
    ForbiddenException

from core.utils.constants import MSG_CREATED, MSG_SUCCESS, HTTP_201_CREATED, HTTP_200_OK, HTTP_204_NO_CONTENT

# 导入所有 Service
from spaces.service.space_service import SpaceService
from spaces.service.space_type_service import SpaceTypeService
from spaces.service.amenity_service import AmenityService

# 导入所有 Serializer
from spaces.api.serializers import (
    SpaceListSerializer, SpaceCreateUpdateSerializer, SpaceBaseSerializer,
    AmenityBaseSerializer, AmenityCreateUpdateSerializer,
    SpaceTypeBaseSerializer, SpaceTypeCreateUpdateSerializer
)

# 导入自定义权限装饰器
from core.decorators import is_system_admin_required, is_admin_or_space_manager_required

logger = logging.getLogger(__name__)

# --- 自定义分页类 (用于 Space) ---
class SpacePagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

# --- Space API Views ---

class SpaceListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated] # 任何认证用户都应能列出空间，创建空间需要特定权限
    filter_backends = []
    search_fields = ['name', 'location', 'description']
    ordering_fields = ['name', 'capacity', 'created_at']

    pagination_class = SpacePagination

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return SpaceCreateUpdateSerializer
        return SpaceListSerializer

    def get_queryset(self):
        user = self.request.user
        service_result = SpaceService().get_all_spaces(user) # Service层负责数据权限过滤
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                paginated_response_data = self.get_paginated_response(serializer.data).data
                return success_response(
                    message=MSG_SUCCESS,
                    data=paginated_response_data,
                    status_code=HTTP_200_OK
                )
            serializer = self.get_serializer(queryset, many=True)
            return success_response(
                message=MSG_SUCCESS,
                data={"results": serializer.data, "count": queryset.count(), "next": None, "previous": None},
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in SpaceListCreateAPIView (list): {e.code} - {e.detail}")
            raise e
        except Exception as e:
            logger.exception("An unhandled exception occurred during space listing in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

    @is_admin_or_space_manager_required # 只有系统管理员或空间管理员能创建空间
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        amenity_ids = validated_data.pop('amenity_ids', [])

        space_data_for_service = validated_data.copy()

        managed_by_instance = validated_data.get('managed_by')
        if managed_by_instance:
            space_data_for_service['managed_by_id'] = managed_by_instance.id
            space_data_for_service.pop('managed_by')

        space_data_for_service['amenity_ids'] = amenity_ids  # Pass amenity_ids to service layer

        try:
            service_result = SpaceService().create_space(user, space_data_for_service)

            if service_result.success:
                response_data = SpaceBaseSerializer(service_result.data).data
                return success_response(
                    message=MSG_CREATED,
                    data=response_data,
                    status_code=HTTP_201_CREATED
                )
            else:
                raise service_result.to_exception()

        except (CustomAPIException, DRFValidationError, DRFNotFound, DRFPermissionDenied, AuthenticationFailed,
                NotAuthenticated) as e:
            logger.warning(f"Known API Exception caught in SpaceListCreateAPIView (create): {type(e).__name__} - {e}")
            raise e
        except DjangoValidationError as e:
            logger.warning(f"DjangoValidationError caught in SpaceListCreateAPIView (create): {e}")
            raise BadRequestException(detail=e.message_dict if hasattr(e, 'message_dict') else str(e))
        except Exception as e:
            logger.exception("An unhandled exception occurred during space creation in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

class SpaceRetrieveUpdateDestroyAPIView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated] # 任何认证用户都应能检索空间，更新和删除需要特定权限
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return SpaceCreateUpdateSerializer
        return SpaceBaseSerializer

    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service_result = SpaceService().get_space_by_id(user, pk) # Service层负责数据权限过滤
        if service_result.success:
            return service_result.data
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
        except (CustomAPIException, DRFNotFound, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(
                f"An unhandled exception occurred during space retrieval for {self.kwargs[self.lookup_field]} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

    @is_admin_or_space_manager_required # 只有系统管理员或空间管理员能更新空间
    def update(self, request, *args, **kwargs):
        instance = self.get_object() # 这里的 get_object 已经包含了查看权限的 Service 逻辑
        serializer = self.get_serializer(instance, data=request.data, partial=kwargs.get('partial', False))
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        amenity_ids = validated_data.pop('amenity_ids', None)

        space_data_for_service = validated_data.copy()

        managed_by_instance = validated_data.get('managed_by')
        if 'managed_by' in validated_data:
            if managed_by_instance:
                space_data_for_service['managed_by_id'] = managed_by_instance.id
            else:
                space_data_for_service['managed_by_id'] = None
            space_data_for_service.pop('managed_by')

        space_data_for_service['amenity_ids'] = amenity_ids

        try:
            # Service 层内部会再次进行对象级权限检查（user.has_perm）
            service_result = SpaceService().update_space(user, instance.pk, space_data_for_service)

            if service_result.success:
                response_data = SpaceBaseSerializer(service_result.data).data
                return success_response(
                    message="空间更新成功。",
                    data=response_data,
                    status_code=HTTP_200_OK
                )
            else:
                raise service_result.to_exception()

        except (CustomAPIException, DRFValidationError, DRFNotFound, DRFPermissionDenied, AuthenticationFailed,
                NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (update): {type(e).__name__} - {e}")
            raise e
        except DjangoValidationError as e:
            logger.warning(f"DjangoValidationError caught in SpaceRetrieveUpdateDestroyAPIView (update): {e}")
            raise BadRequestException(detail=e.message_dict if hasattr(e, 'message_dict') else str(e))
        except Exception as e:
            logger.exception(f"An unhandled exception occurred during space update for {instance.pk} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

    @is_admin_or_space_manager_required # 只有系统管理员或空间管理员能删除空间
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object() # 这里的 get_object 已经包含了查看权限的 Service 逻辑
        user = request.user

        try:
            # Service 层内部会再次进行对象级权限检查（user.has_perm）
            service_result = SpaceService().delete_space(user, instance.pk)

            if service_result.success:
                return success_response(
                    message="空间删除成功。",
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()

        except (CustomAPIException, DRFNotFound, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (delete): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"An unhandled exception occurred during space deletion for {instance.pk} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

# --- SpaceType API Views ---

class SpaceTypeListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated] # 任何认证用户都可以列出 SpaceType，创建需要特定权限
    pagination_class = None

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return SpaceTypeCreateUpdateSerializer
        return SpaceTypeBaseSerializer

    def get_queryset(self):
        user = self.request.user
        service_result = SpaceTypeService().get_all_space_types(user) # Service层负责数据权限过滤
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset, many=True)
            return success_response(
                message=MSG_SUCCESS,
                data={"results": serializer.data, "count": queryset.count(), "next": None, "previous": None},
                status_code=HTTP_200_OK
            )
        except (CustomAPIException, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(f"Known API Exception caught in SpaceTypeListView (list): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception("An unhandled exception occurred during space type listing in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

    @is_system_admin_required # 只有系统管理员能创建空间类型
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        try:
            service_result = SpaceTypeService().create_space_type(user, validated_data)

            if service_result.success:
                response_data = SpaceTypeBaseSerializer(service_result.data).data
                return success_response(
                    message=MSG_CREATED,
                    data=response_data,
                    status_code=HTTP_201_CREATED
                )
            else:
                raise service_result.to_exception()

        except (CustomAPIException, DRFValidationError, DRFNotFound, DRFPermissionDenied, AuthenticationFailed,
                NotAuthenticated) as e:
            logger.warning(f"Known API Exception caught in SpaceTypeListView (create): {type(e).__name__} - {e}")
            raise e
        except DjangoValidationError as e:
            logger.warning(f"DjangoValidationError caught in SpaceTypeListView (create): {e}")
            raise BadRequestException(detail=e.message_dict if hasattr(e, 'message_dict') else str(e))
        except Exception as e:
            logger.exception("An unhandled exception occurred during space type creation in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

class SpaceTypeDetailUpdateDestroyView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated] # 任何认证用户都可以检索 SpaceType，更新和删除需要特定权限
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return SpaceTypeCreateUpdateSerializer
        return SpaceTypeBaseSerializer

    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service_result = SpaceTypeService().get_space_type_by_id(user, pk) # Service层负责数据权限过滤
        if service_result.success:
            return service_result.data
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
        except (CustomAPIException, DRFNotFound, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in SpaceTypeDetailUpdateDestroyView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(
                f"An unhandled exception occurred during space type retrieval for {self.kwargs[self.lookup_field]} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

    @is_system_admin_required # 只有系统管理员能更新空间类型
    def update(self, request, *args, **kwargs):
        instance = self.get_object() # 这里的 get_object 已经包含了查看权限的 Service 逻辑
        serializer = self.get_serializer(instance, data=request.data, partial=kwargs.get('partial', False))
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        try:
            service_result = SpaceTypeService().update_space_type(user, instance.pk, validated_data)

            if service_result.success:
                response_data = SpaceTypeBaseSerializer(service_result.data).data
                return success_response(
                    message="空间类型更新成功。",
                    data=response_data,
                    status_code=HTTP_200_OK
                )
            else:
                raise service_result.to_exception()

        except (CustomAPIException, DRFValidationError, DRFNotFound, DRFPermissionDenied, AuthenticationFailed,
                NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in SpaceTypeDetailUpdateDestroyView (update): {type(e).__name__} - {e}")
            raise e
        except DjangoValidationError as e:
            logger.warning(f"DjangoValidationError caught in SpaceTypeDetailUpdateDestroyView (update): {e}")
            raise BadRequestException(detail=e.message_dict if hasattr(e, 'message_dict') else str(e))
        except Exception as e:
            logger.exception(f"An unhandled exception occurred during space type update for {instance.pk} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

    @is_system_admin_required # 只有系统管理员能删除空间类型
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object() # 这里的 get_object 已经包含了查看权限的 Service 逻辑
        user = request.user

        try:
            service_result = SpaceTypeService().delete_space_type(user, instance.pk)

            if service_result.success:
                return success_response(
                    message="空间类型删除成功。",
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()

        except (CustomAPIException, DRFNotFound, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in SpaceTypeDetailUpdateDestroyView (delete): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(
                f"An unhandled exception occurred during space type deletion for {instance.pk} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

# --- Amenity API Views ---

class AmenityListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated] # 任何认证用户都可以列出 Amenity，创建需要特定权限
    pagination_class = None

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AmenityCreateUpdateSerializer
        return AmenityBaseSerializer

    def get_queryset(self):
        user = self.request.user
        service_result = AmenityService().get_all_amenities(user) # Service层负责数据权限过滤
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset, many=True)
            return success_response(
                message=MSG_SUCCESS,
                data={"results": serializer.data, "count": queryset.count(), "next": None, "previous": None},
                status_code=HTTP_200_OK
            )
        except (CustomAPIException, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(f"Known API Exception caught in AmenityListView (list): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception("An unhandled exception occurred during amenity listing in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

    @is_admin_or_space_manager_required # 只有系统管理员或空间管理员能创建设施类型
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        try:
            service_result = AmenityService().create_amenity(user, validated_data)

            if service_result.success:
                response_data = AmenityBaseSerializer(service_result.data).data
                return success_response(
                    message=MSG_CREATED,
                    data=response_data,
                    status_code=HTTP_201_CREATED
                )
            else:
                raise service_result.to_exception()

        except (CustomAPIException, DRFValidationError, DRFNotFound, DRFPermissionDenied, AuthenticationFailed,
                NotAuthenticated) as e:
            logger.warning(f"Known API Exception caught in AmenityListView (create): {type(e).__name__} - {e}")
            raise e
        except DjangoValidationError as e:
            logger.warning(f"DjangoValidationError caught in AmenityListView (create): {e}")
            raise BadRequestException(detail=e.message_dict if hasattr(e, 'message_dict') else str(e))
        except Exception as e:
            logger.exception("An unhandled exception occurred during amenity creation in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

class AmenityDetailUpdateDestroyView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated] # 任何认证用户都可以检索 Amenity，更新和删除需要特定权限
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return AmenityCreateUpdateSerializer
        return AmenityBaseSerializer

    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service_result = AmenityService().get_amenity_by_id(user, pk) # Service层负责数据权限过滤
        if service_result.success:
            return service_result.data
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
        except (CustomAPIException, DRFNotFound, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in AmenityDetailUpdateDestroyView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(
                f"An unhandled exception occurred during amenity retrieval for {self.kwargs[self.lookup_field]} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

    @is_admin_or_space_manager_required # 只有系统管理员或空间管理员能更新设施类型
    def update(self, request, *args, **kwargs):
        instance = self.get_object() # 这里的 get_object 已经包含了查看权限的 Service 逻辑
        serializer = self.get_serializer(instance, data=request.data, partial=kwargs.get('partial', False))
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        try:
            service_result = AmenityService().update_amenity(user, instance.pk, validated_data)

            if service_result.success:
                response_data = AmenityBaseSerializer(service_result.data).data
                return success_response(
                    message="设施类型更新成功。",
                    data=response_data,
                    status_code=HTTP_200_OK
                )
            else:
                raise service_result.to_exception()

        except (CustomAPIException, DRFValidationError, DRFNotFound, DRFPermissionDenied, AuthenticationFailed,
                NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in AmenityDetailUpdateDestroyView (update): {type(e).__name__} - {e}")
            raise e
        except DjangoValidationError as e:
            logger.warning(f"DjangoValidationError caught in AmenityDetailUpdateDestroyView (update): {e}")
            raise BadRequestException(detail=e.message_dict if hasattr(e, 'message_dict') else str(e))
        except Exception as e:
            logger.exception(f"An unhandled exception occurred during amenity update for {instance.pk} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

    @is_admin_or_space_manager_required # 只有系统管理员或空间管理员能删除设施类型
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object() # 这里的 get_object 已经包含了查看权限的 Service 逻辑
        user = request.user

        try:
            service_result = AmenityService().delete_amenity(user, instance.pk)

            if service_result.success:
                return success_response(
                    message="设施类型删除成功。",
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()

        except (CustomAPIException, DRFNotFound, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in AmenityDetailUpdateDestroyView (delete): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"An unhandled exception occurred during amenity deletion for {instance.pk} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])