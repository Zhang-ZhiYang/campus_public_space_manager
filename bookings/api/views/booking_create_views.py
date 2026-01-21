# bookings/api/views/booking_create_views.py
import logging
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError
from bookings.api.serializers import BookingCreateSerializer

logger = logging.getLogger(__name__)


class BookingCreateAPIView(generics.CreateAPIView):
    """
    创建预订的 API 视图。
    调用 BookingPreliminaryService 进行初步校验，并触发异步深层校验和创建。
    返回 HTTP 202 Accepted 表示请求已接受但正在异步处理中。
    """
    serializer_class = BookingCreateSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        result_data = serializer.save() # 这里会调用 BookingCreateSerializer.create

        http_status_code = result_data.get('status_code', status.HTTP_202_ACCEPTED)
        message = result_data.get('message', "预订请求已提交，正在处理中。")
        response_payload = {
            'booking_id': result_data.get('id'), # 现在这里就是 'id'
            'request_uuid': str(result_data.get('request_uuid'))
        }

        if http_status_code == status.HTTP_200_OK:
            message = "请求已在处理中或已完成，返回现有预订信息。"

        return success_response(
            message=message,
            data=response_payload,
            status_code=http_status_code
        )