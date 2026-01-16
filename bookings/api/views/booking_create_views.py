# bookings/api/views/booking_create_views.py
import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError
from core.utils.constants import MSG_SUCCESS, HTTP_202_ACCEPTED, HTTP_200_OK

from bookings.api.serializers import BookingCreateSerializer, BookingSerializer
from bookings.service.booking_service import BookingService
from bookings.models import BOOKING_STATUS_CHOICES, PROCESSING_STATUS_CHOICES

logger = logging.getLogger(__name__)


class BookingCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]  # 认证用户才能创建预订

    def post(self, request, *args, **kwargs):
        serializer = BookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)  # DRF 自动处理 400 Bad Request

        user = request.user
        validated_data = serializer.validated_data

        booking_service = BookingService()

        try:
            service_result = booking_service.create_initial_booking(
                user=user,
                request_data={
                    'space_id': validated_data.get('space_id'),
                    'bookable_amenity_id': validated_data.get('bookable_amenity_id'),
                    'start_time': validated_data['start_time'],
                    'end_time': validated_data['end_time'],
                    'booked_quantity': validated_data.get('booked_quantity'),
                    'purpose': validated_data.get('purpose', ''),
                    'request_uuid': validated_data.get('request_uuid'),
                    'expected_attendees': validated_data.get('expected_attendees'),
                }
            )

            if service_result.success:
                http_status = service_result.status_code
                message_text = "预订请求已接受，正在处理中。" if service_result.status_code == HTTP_202_ACCEPTED else service_result.message

                # 重新序列化 ServiceResult.data (Dict[str, Any]) 以统一输出格式
                serializer = BookingSerializer(service_result.data)  # 使用 BookingSerializer 来格式化返回数据

                return success_response(
                    message=message_text,
                    data=serializer.data,
                    status_code=http_status
                )
            else:
                raise service_result.to_exception()

        except CustomAPIException as e:
            logger.warning(f"BookingCreateAPIView CustomAPIException: {e.code} - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"BookingCreateAPIView unhandled error: {e}")
            raise InternalServerError(detail="服务器内部错误。")