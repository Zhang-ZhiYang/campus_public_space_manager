# check_in/api/views.py
import logging

from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from core.utils.response import success_response
from core.utils.constants import HTTP_200_OK, HTTP_201_CREATED

from check_in.api.serializers import QRCheckInSerializer, CheckInRecordSerializer
from check_in.service.check_in_service import CheckInService
from core.utils.exceptions import CustomAPIException, InternalServerError # 导入 CustomAPIException

logger = logging.getLogger(__name__)

class CheckInAPIView(APIView):
    """
    签到接口：允许用户或工作人员对指定预订进行签到操作。
    支持拍照、扫码、定位多种签到方式。
    - POST /api/v1/check-in/bookings/<int:booking_pk>/
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser] # 支持文件上传 (用于签到图片)

    def post(self, request, booking_pk: int):
        """
        执行签到操作。
        """
        # 使用 QRCheckInSerializer 作为通用请求序列化器，因为它包含了 photo, latitude, longitude
        # 且将经纬度设置为可选，可以在 Service 层根据实际的 effective_check_in_method 进行强制校验。
        serializer = QRCheckInSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        photo_file = validated_data.get('photo')
        latitude = validated_data.get('latitude')
        longitude = validated_data.get('longitude')
        notes = validated_data.get('notes', '')

        # 客户端请求方式标识 (用于 Service 内部逻辑 판단和记录)
        # 这里为了简化，假设客户端会有某种方式告知它的"意图"，可以更加精细化
        # 例如，可以根据 `latitude`/`longitude` 是否存在判断为 LOCATION，
        # 或者在请求路径中区分 (如 POST /check-in/bookings/<pk>/location-checkin/)
        # 这里使用一个简单的 'GENERIC_USER_CHECKIN'，Service 内部会根据 space config 进行更严格的判断
        # 如果需要区分扫码/拍照/手动自行签到，前端需要在 body 或 header 中传递一个 method type。
        # 为保持 Service 内部判断的灵活性，这里暂时传递一个通用标识。
        client_request_method = 'GENERIC_USER_CHECKIN'
        if photo_file:
            client_request_method = 'PHOTO'
        if latitude is not None and longitude is not None:
             client_request_method = 'LOCATION'

        try:
            check_in_service = CheckInService.get_instance()
            service_result = check_in_service.perform_check_in(
                user=user,
                booking_pk=booking_pk,
                client_request_method=client_request_method,
                latitude=latitude,
                longitude=longitude,
                photo=photo_file,
                notes=notes
            )

            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=service_result.data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception() # ServiceResult 失败则转换为 CustomAPIException
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in CheckInAPIView (post): {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"执行签到失败 (Booking PK: {booking_pk})，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误，签到失败。")

class CheckInRecordDetailAPIView(APIView):
    """
    签到记录详情接口：允许用户或工作人员获取指定预订的签到详情。
    - GET /api/v1/check-in/records/<int:booking_pk>/
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, booking_pk: int):
        """
        获取单个预订的签到记录详情。
        """
        user = request.user
        try:
            check_in_service = CheckInService.get_instance()
            service_result = check_in_service.get_check_in_record_by_booking(
                user=user,
                booking_pk=booking_pk
            )

            if service_result.success:
                # 签到详情序列化器，用于在响应中格式化数据
                serializer = CheckInRecordSerializer(service_result.data, context={'request': request})
                return success_response(
                    message=service_result.message,
                    data=serializer.data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in CheckInRecordDetailAPIView (get): {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"获取签到记录失败 (Booking PK: {booking_pk})，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误，获取签到记录失败。")