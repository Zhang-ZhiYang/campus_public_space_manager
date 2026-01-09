# bookings/api/views.py
from rest_framework.views import APIView
from rest_framework.response import Response as DRFResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.exceptions import NotFound as DRFNotFound, PermissionDenied as DRFPermissionDenied, AuthenticationFailed, NotAuthenticated

from bookings.service.booking_service import BookingService
from core.utils.constants import HTTP_200_OK
from core.utils.response import success_response, error_response
from core.utils.exceptions import CustomAPIException, ServiceException, BadRequestException, NotFoundException, \
    ForbiddenException

from bookings.api.serializers import (
    BookingCreateSerializer, BookingDetailSerializer, BookingShortSerializer,
    BookingStatusUpdateSerializer
)

# 导入自定义权限装饰器
from core.decorators import is_admin_or_space_manager_required

logger = logging.getLogger(__name__)

# ... (BookingCreateAPIView, UserBookingListAPIView 保持不变) ...

class BookingCreateAPIView(APIView):
    permission_classes = [IsAuthenticated] # 任何认证用户都应该能尝试创建预订
    booking_service = BookingService()

    # 预订的实际权限检查（如能否预订特定空间/设施）在 Service 层处理
    def post(self, request, *args, **kwargs):
        serializer = BookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        space_id = validated_data.get('space').id if validated_data.get('space') else None
        bookable_amenity_id = validated_data.get('bookable_amenity').id if validated_data.get(
            'bookable_amenity') else None

        booking_details = {
            'start_time': validated_data['start_time'],
            'end_time': validated_data['end_time'],
            'purpose': validated_data['purpose'],
            'booked_quantity': validated_data['booked_quantity'],
            'space_id': space_id,
            'bookable_amenity_id': bookable_amenity_id,
        }

        try:
            service_result = self.booking_service.create_booking(user, booking_details)

            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data={"booking_id": service_result.data.id},
                    status_code=service_result.status_code
                )
            else:
                return error_response(
                    message=service_result.message,
                    error={"code": service_result.error_code,
                           "detail": service_result.errors or [service_result.message]},
                    status_code=service_result.status_code
                )

        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in BookingCreateAPIView: {e.code} - {e.detail}")
            return error_response(
                message=str(e.detail),
                error={"code": e.code, "detail": e.detail},
                status_code=e.status_code
            )
        except ServiceException as e:
            logger.error(f"ServiceException caught in BookingCreateAPIView: {e.error_code} - {e.message}",
                         exc_info=True)
            return error_response(
                message=e.message,
                error={"code": e.error_code, "detail": e.errors or [str(e)]},
                status_code=e.status_code
            )
        except DjangoValidationError as e:
            logger.warning(f"DjangoValidationError caught in BookingCreateAPIView: {e}")
            errors_detail = {}
            if hasattr(e, 'error_dict'):
                errors_detail = {field: [str(err) for err in msgs] for field, msgs in e.error_dict.items()}
            elif hasattr(e, 'message_dict'):
                errors_detail = {field: [str(err) for err in msgs] for field, msgs in e.message_dict.items()}
            else:
                errors_detail = {"non_field_errors": [str(e)]}

            return error_response(
                message="数据验证失败。",
                error={"code": BadRequestException.default_code, "detail": errors_detail},
                status_code=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception("An unhandled exception occurred during booking creation in API view.")
            return error_response(
                message="服务器内部错误。",
                error={"code": "server_error", "detail": str(e)},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class UserBookingListAPIView(ListAPIView):
    """
    获取当前用户的所有预订列表。
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BookingShortSerializer
    booking_service = BookingService()

    def get_queryset(self):
        return self.booking_service.get_user_bookings(self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            paginated_response_data = self.get_paginated_response(serializer.data).data
            return success_response(
                message="成功获取用户预订列表。",
                data=paginated_response_data,
                status_code=status.HTTP_200_OK
            )

        serializer = self.get_serializer(queryset, many=True)
        return success_response(
            message="成功获取用户预订列表。",
            data={"results": serializer.data, "count": queryset.count(), "next": None, "previous": None},
            status_code=status.HTTP_200_OK
        )

class BookingListAPIView(ListAPIView):
    """
    获取所有预订列表 (仅限管理员访问)。
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BookingShortSerializer
    booking_service = BookingService()

    @is_admin_or_space_manager_required # 只有系统管理员或空间管理员能查看所有预订
    def get_queryset(self):
        service_result = self.booking_service.get_all_bookings(self.request.user) # Service层负责数据权限过滤
        if not service_result.success:
            raise ForbiddenException(detail=service_result.message)
        return service_result.data

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                paginated_response_data = self.get_paginated_response(serializer.data).data
                return success_response(
                    message="成功获取所有预订列表。",
                    data=paginated_response_data,
                    status_code=status.HTTP_200_OK
                )

            serializer = self.get_serializer(queryset, many=True)
            return success_response(
                message="成功获取所有预订列表。",
                data={"results": serializer.data, "count": queryset.count(), "next": None, "previous": None},
                status_code=HTTP_200_OK
            )
        except (CustomAPIException, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(f"Known API Exception caught in BookingListAPIView (list): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception("An unhandled exception occurred during booking list all in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

class BookingRetrieveAPIView(RetrieveAPIView):
    """
    获取单个预订详情。
    """
    permission_classes = [IsAuthenticated] # 任何认证用户都应能检索自己的预订，管理员可检索所有
    serializer_class = BookingDetailSerializer
    booking_service = BookingService()
    lookup_field = 'pk'

    def get_queryset(self):
        # Service层无需显式过滤，它只返回一个对象。权限逻辑在 Service.get_booking_by_id 中处理。
        # 这里只返回一个基础查询集，以便 DRF 的 get_object 可以工作
        return self.booking_service.booking_dao.get_queryset()

    def get_object(self):
        # 在这里直接通过 Service 获取，Service 内部会进行对象级权限的检查
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service_result = self.booking_service.get_booking(user, pk) # 假设 BookingService 有 get_booking 方法
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception() # Service 内部会抛出 ForbiddenException 等

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return success_response(
                message="成功获取预订详情。",
                data=serializer.data,
                status_code=status.HTTP_200_OK
            )
        except (CustomAPIException, DRFNotFound, DRFPermissionDenied, AuthenticationFailed, NotAuthenticated) as e:
            logger.warning(
                f"Known API Exception caught in BookingRetrieveAPIView (retrieve): {type(e).__name__} - {e}")
            raise e
        except Exception as e:
            logger.exception(
                f"An unhandled exception occurred during booking retrieval for {self.kwargs[self.lookup_field]} in API view.")
            raise ServiceException(message="服务器内部错误。", error_code="server_error",
                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, errors=[str(e)])

class BookingCancelAPIView(APIView):
    """
    取消指定预订。
    """
    permission_classes = [IsAuthenticated] # 任何认证用户都应能取消自己的预订，管理员可取消所有
    booking_service = BookingService()

    def post(self, request, pk, *args, **kwargs):
        user = request.user
        booking_id = pk

        try:
            # Service 层内部会进行对象级权限检查（用户是否是预订者本人 或 拥有管理权限）
            service_result = self.booking_service.cancel_booking(user, booking_id)

            if service_result.success:
                response_data = BookingDetailSerializer(service_result.data).data
                return success_response(
                    message=service_result.message,
                    data=response_data,
                    status_code=service_result.status_code
                )
            else:
                return error_response(
                    message=service_result.message,
                    error={"code": service_result.error_code,
                           "detail": service_result.errors or [service_result.message]},
                    status_code=service_result.status_code
                )
        except DRFNotFound: # Service层抛出的NotFound会被统一异常处理为DRFNotFound
            return error_response(
                message="预订不存在。",
                error={"code": NotFoundException.default_code, "detail": "指定的预订ID无效。"},
                status_code=status.HTTP_404_NOT_FOUND
            )
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in BookingCancelAPIView: {e.code} - {e.detail}")
            return error_response(
                message=str(e.detail),
                error={"code": e.code, "detail": e.detail},
                status_code=e.status_code
            )
        except ServiceException as e:
            logger.error(f"ServiceException caught in BookingCancelAPIView: {e.error_code} - {e.message}",
                         exc_info=True)
            return error_response(
                message=e.message,
                error={"code": e.error_code, "detail": e.errors or [str(e)]},
                status_code=e.status_code
            )
        except Exception as e:
            logger.exception(f"An unhandled exception occurred during booking cancellation for booking {booking_id}.")
            return error_response(
                message="服务器内部错误。",
                error={"code": "server_error", "detail": str(e)},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class BookingStatusUpdateAPIView(APIView):
    """
    管理员或空间管理员更新预订状态 (批准、拒绝、签到、签出、未到场、完成)。
    """
    permission_classes = [IsAuthenticated] # 认证用户才能访问，但具体操作需要角色权限
    booking_service = BookingService()

    @is_admin_or_space_manager_required # 只有系统管理员或空间管理员能更新预订状态
    def post(self, request, pk, *args, **kwargs):
        serializer = BookingStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        new_status = serializer.validated_data['status']
        admin_notes = serializer.validated_data.get('admin_notes')

        booking_id = pk

        try:
            # Service 层内部会进行对象级权限检查（如 can_approve_booking 或 can_check_in_booking）
            service_result = self.booking_service.update_booking_status(user, booking_id, new_status, admin_notes)

            if service_result.success:
                response_data = BookingDetailSerializer(service_result.data).data
                return success_response(
                    message=service_result.message,
                    data=response_data,
                    status_code=service_result.status_code
                )
            else:
                return error_response(
                    message=service_result.message,
                    error={"code": service_result.error_code,
                           "detail": service_result.errors or [service_result.message]},
                    status_code=service_result.status_code
                )
        except DRFNotFound:
            return error_response(
                message="预订不存在。",
                error={"code": NotFoundException.default_code, "detail": "指定的预订ID无效。"},
                status_code=status.HTTP_404_NOT_FOUND
            )
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in BookingStatusUpdateAPIView: {e.code} - {e.detail}")
            return error_response(
                message=str(e.detail),
                error={"code": e.code, "detail": e.detail},
                status_code=e.status_code
            )
        except ServiceException as e:
            logger.error(f"ServiceException caught in BookingStatusUpdateAPIView: {e.error_code} - {e.message}",
                         exc_info=True)
            return error_response(
                message=e.message,
                error={"code": e.error_code, "detail": e.errors or [str(e)]},
                status_code=e.status_code
            )
        except Exception as e:
            logger.exception(f"An unhandled exception occurred during booking status update for booking {booking_id}.")
            return error_response(
                message="服务器内部错误。",
                error={"code": "server_error", "detail": str(e)},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )