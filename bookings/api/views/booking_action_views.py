# bookings/api/views/booking_action_views.py
import logging
import uuid

from rest_framework.views import APIView
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.service import ServiceFactory
from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError, BadRequestException
from core.decorators import is_admin_or_space_manager_required  # <-- 确保依然导入这个装饰器

from bookings.api.serializers import BookingUpdateStatusSerializer, BookingStatusSerializer, \
    BookingMarkNoShowSerializer, BookingDetailSerializer
from bookings.service.booking_service import BookingService
from bookings.service.booking_status_query_service import BookingStatusQueryService
from bookings.service.violation_service import ViolationService

logger = logging.getLogger(__name__)


class BookingCancelAPIView(APIView):
    """
    自定义动作：取消预订。
    POST /bookings/<int:pk>/cancel/
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        user = request.user
        reason = request.data.get('reason', '用户主动取消')
        if not reason:
            raise BadRequestException(detail="取消预订必须提供原因。", code="missing_cancel_reason")

        try:
            booking_service = BookingService()
            service_result = booking_service.cancel_booking(user, pk, reason)

            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=None,
                    status_code=status.HTTP_200_OK
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            logger.warning(f"Known API Exception caught in BookingCancelAPIView:  {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"取消预订失败 (ID: {pk}, User: {user.username})。")
            raise InternalServerError(detail="服务器内部错误。")


class BookingStatusUpdateAPIView(APIView):
    """
    自定义动作：管理员更新预订状态。
    PATCH /bookings/<int:pk>/status/
    """
    permission_classes = [IsAuthenticated]  # <-- 仅保留 IsAuthenticated

    @is_admin_or_space_manager_required  # <-- 将装饰器直接应用于 patch 方法
    def patch(self, request, pk, *args, **kwargs):
        user = request.user
        serializer = BookingUpdateStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_status = serializer.validated_data['status']
        admin_notes = serializer.validated_data.get('admin_notes')

        logger.info(
            f"[View.patch] Booking PK={pk}, User='{user.username}' is attempting to change status to '{new_status}'.")
        logger.debug(
            f"[View.patch] new_status from serializer.validated_data: '{new_status}', admin_notes: '{admin_notes}'")

        try:
            booking_service = BookingService()
            service_result = booking_service.update_booking_status(user, pk, new_status, admin_notes)
            logger.debug(
                f"[View] Service result success: {service_result.success}, data type: {type(service_result.data)}")
            if service_result.success:
                logger.debug(
                    f"[View] Service result data (before serialization) status: {getattr(service_result.data, 'status', 'N/A')}")
                response_serializer = BookingDetailSerializer(service_result.data, context={'request': request})
                logger.debug(
                    f"[View] Serialized data (for response) status: {response_serializer.data.get('status_display')}")
                return success_response(
                    message=service_result.message,
                    data=response_serializer.data,
                    status_code=status.HTTP_200_OK
                )
            else:
                logger.error(f"[View.patch] Service call failed for Booking PK={pk}. Errors: {service_result.errors}")
                raise service_result.to_exception()
        except CustomAPIException as e:
            logger.warning(f"[View.patch] CustomAPIException caught for Booking PK={pk}: - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(
                f"[View.patch] Unhandled error changing booking status (ID: {pk}, User: {user.username}, New Status: {new_status}).")
            raise InternalServerError(detail="服务器内部错误。")


class BookingMarkNoShowAPIView(APIView):
    """
    自定义动作：批量标记预订为未到场并创建违规记录。
    POST /bookings/mark-no-show/
    """
    permission_classes = [IsAuthenticated]  # <-- 仅保留 IsAuthenticated

    @is_admin_or_space_manager_required  # <-- 将装饰器直接应用于 post 方法
    def post(self, request, *args, **kwargs):  # 这里也需要改为使用正确的装饰器
        user = request.user
        serializer = BookingMarkNoShowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        pk_list = serializer.validated_data['pk_list']

        try:
            violation_service = ServiceFactory.get_service('ViolationService')
            service_result = violation_service.mark_no_show_and_violate(user, pk_list)

            if service_result.success:
                no_show_count, violation_count = service_result.data
                message = f"成功标记 {no_show_count} 条预订为未到场，创建 {violation_count} 条违规记录。"
                if service_result.warnings:
                    message += f" 警告: {'; '.join(service_result.warnings)}"

                return success_response(
                    message=message,
                    data={'no_show_count': no_show_count, 'violation_count': violation_count},
                    status_code=status.HTTP_200_OK
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            logger.warning(f"Known API Exception caught in BookingMarkNoShowAPIView:  - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"批量标记未到场并创建违规记录失败 (User: {user.username}, PK List: {pk_list})。")
            raise InternalServerError(detail="服务器内部错误。")


class BookingGetStatusAPIView(generics.RetrieveAPIView):
    """
    查询单个预订的状态信息。
    GET /bookings/<int:pk>/status/ 或 /bookings/<uuid:request_uuid>/status/
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BookingStatusSerializer
    lookup_field = 'pk'

    def get_object(self):
        user = self.request.user
        track_id = self.kwargs.get(self.lookup_field) or self.kwargs.get('request_uuid')

        if not track_id:
            raise BadRequestException(detail="缺少跟踪ID (booking ID 或 request_uuid)。")

        try:
            if self.kwargs.get('pk'):
                track_id = int(track_id)
            elif self.kwargs.get('request_uuid'):
                track_id = uuid.UUID(str(track_id))
        except ValueError:
            raise BadRequestException(detail="跟踪ID格式无效。", code="invalid_track_id_format")

        booking_status_query_service = ServiceFactory.get_service('BookingStatusQueryService')
        service_result = booking_status_query_service.get_booking_status_info(user, track_id)

        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        try:
            instance_data = self.get_object()
            serializer = self.get_serializer(instance_data)
            return success_response(
                message="成功获取预订状态。",
                data=serializer.data,
                status_code=status.HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"Known API Exception caught in BookingGetStatusAPIView: - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(
                f"查询预订状态失败 (Track ID: {self.kwargs.get(self.lookup_field)}/{self.kwargs.get('request_uuid')})。")
            raise InternalServerError(detail="服务器内部错误。")
