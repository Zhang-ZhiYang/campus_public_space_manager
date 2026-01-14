# spaces/api/views.py
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from core.pagination import CustomPageNumberPagination

import logging

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError

from core.utils.constants import MSG_CREATED, MSG_SUCCESS, HTTP_201_CREATED, HTTP_200_OK, HTTP_204_NO_CONTENT
from spaces.api.filters import SpaceFilter

from spaces.service.space_service import SpaceService
from spaces.service.space_type_service import SpaceTypeService
from spaces.service.amenity_service import AmenityService

from spaces.api.serializers import (
    SpaceListSerializer, SpaceCreateUpdateSerializer, SpaceBaseSerializer,
    AmenityBaseSerializer, AmenityCreateUpdateSerializer,
    SpaceTypeBaseSerializer, SpaceTypeCreateUpdateSerializer
)

from core.decorators import is_system_admin_required, is_admin_or_space_manager_required

logger = logging.getLogger(__name__)

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
        service_result = SpaceService().get_all_spaces(user)
        if service_result.success:
            # 注意: SpaceService.get_all_spaces 返回 List[Dict]，但 DRF 的 ListCreateAPIView
            # 期望 get_queryset() 返回 QuerySet。这里为了兼容已修改的服务层，
            # get_queryset 返回的将是 List[Dict]。这意味着分页和过滤可能需要适应其行为。
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            # self.get_queryset() 返回的是 List[Dict]，而不是 QuerySet
            queryset = self.filter_queryset(self.get_queryset())

            page = None
            if self.pagination_class:
                request.successful_response_status = HTTP_200_OK
                page = self.paginate_queryset(queryset) # 如果 queryset 是 list，Pagination 也能处理

            serializer = self.get_serializer(page if page is not None else queryset, many=True)

            if page is not None:
                return self.get_paginated_response(serializer.data)
            else:
                # FIX: 使用 len(queryset) 而不是 queryset.count()
                return success_response(
                    message=MSG_SUCCESS,
                    data={"count": len(queryset), "next": None, "previous": None, "results": serializer.data},
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

        user = request.user
        validated_data = serializer.validated_data

        amenity_ids = validated_data.pop('amenity_ids', [])

        space_data_for_service = validated_data.copy()

        if 'managed_by' in space_data_for_service:
            mb_instance = space_data_for_service.pop('managed_by')
            space_data_for_service['managed_by_id'] = mb_instance.pk if mb_instance else None

        if 'space_type' in space_data_for_service:
            st_instance = space_data_for_service.pop('space_type')
            space_data_for_service['space_type_id'] = st_instance.pk if st_instance else None

        if 'parent_space' in space_data_for_service:
            ps_instance = space_data_for_service.pop('parent_space')
            space_data_for_service['parent_space_id'] = ps_instance.pk if ps_instance else None

        space_data_for_service['amenity_ids'] = amenity_ids

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
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"获取空间详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_admin_or_space_manager_required
    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        amenity_ids = validated_data.pop('amenity_ids', None)

        space_data_for_service = validated_data.copy()

        if 'managed_by' in space_data_for_service:
            mb_instance = space_data_for_service.pop('managed_by')
            space_data_for_service['managed_by_id'] = mb_instance.pk if mb_instance else None
        elif 'managed_by_id' in request.data and request.data['managed_by_id'] is None:
            space_data_for_service['managed_by_id'] = None

        if 'space_type' in space_data_for_service:
            st_instance = space_data_for_service.pop('space_type')
            space_data_for_service['space_type_id'] = st_instance.pk if st_instance else None

        if 'parent_space' in space_data_for_service:
            ps_instance = space_data_for_service.pop('parent_space')
            space_data_for_service['parent_space_id'] = ps_instance.pk if ps_instance else None

        space_data_for_service['amenity_ids'] = amenity_ids

        try:
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

        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceRetrieveUpdateDestroyAPIView (update): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"更新空间失败 (ID: {instance.pk})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_admin_or_space_manager_required
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        user = request.user

        try:
            service_result = SpaceService().delete_space(user, instance.pk)

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
            logger.exception(f"删除空间失败 (ID: {instance.pk})。")
            raise InternalServerError(detail="服务器内部错误。")

# --- SpaceType API Views ---

class SpaceTypeListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = None # Explicitly no pagination for this view

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return SpaceTypeCreateUpdateSerializer
        return SpaceTypeBaseSerializer

    def get_queryset(self):
        user = self.request.user
        service_result = SpaceTypeService().get_all_space_types(user)
        if service_result.success:
            # 这里返回的是 List[Dict]，而不是 QuerySet
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            # self.filter_queryset 也将接收并处理 List[Dict] (如果 filterset 支持)
            queryset = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset, many=True)
            return success_response(
                message=MSG_SUCCESS,
                # FIX: 使用 len() 获取列表长度
                data={"results": serializer.data, "count": len(queryset)},
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
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceTypeDetailUpdateDestroyView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"获取空间类型详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
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

        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceTypeDetailUpdateDestroyView (update): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"更新空间类型失败 (ID: {instance.pk})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
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

        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in SpaceTypeDetailUpdateDestroyView (delete): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"删除空间类型失败 (ID: {instance.pk})。")
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
        user = self.request.user
        service_result = AmenityService().get_all_amenities(user)
        if service_result.success:
            # 这里返回的是 List[Dict]
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset, many=True)
            return success_response(
                message=MSG_SUCCESS,
                # FIX: 使用 len() 获取列表长度
                data={"results": serializer.data, "count": len(queryset)},
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
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in AmenityDetailUpdateDestroyView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"获取设施类型详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
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

        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in AmenityDetailUpdateDestroyView (update): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"更新设施类型失败 (ID: {instance.pk})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_system_admin_required
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
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

        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in AmenityDetailUpdateDestroyView (delete): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(f"删除设施类型失败 (ID: {instance.pk})。")
            raise InternalServerError(detail=f"服务器内部错误: {str(e)}")