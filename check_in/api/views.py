# check_in/api/views.py
import logging

from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from core.utils.response import success_response
from core.utils.constants import HTTP_200_OK, HTTP_201_CREATED, HTTP_403_FORBIDDEN

from check_in.api.serializers import QRCheckInSerializer, CheckInRecordSerializer
from check_in.service.check_in_service import CheckInService
from core.utils.exceptions import CustomAPIException, InternalServerError, ForbiddenException, BadRequestException # 导入 BadRequestException

from bookings.models import Booking

logger = logging.getLogger(__name__)

class CheckInAPIView(APIView):
    """
    签到接口：允许用户自行签到，或工作人员（系统管理员、空间管理员、签到员）代签。
    支持拍照、扫码、定位多种签到方式。
    - POST /api/v1/check-in/bookings/<int:booking_pk>/
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request, booking_pk: int):
        """
        执行签到操作。
        根据前端提供的签到方式和数据进行签到。
        """
        try:
            serializer = QRCheckInSerializer(data=request.data, context={'request': request})
            serializer.is_valid(raise_exception=True)

            user = request.user
            validated_data = serializer.validated_data

            photo_file = validated_data.get('photo')
            latitude = validated_data.get('latitude')
            longitude = validated_data.get('longitude')
            notes = validated_data.get('notes', '')
            client_check_in_method = validated_data.get('client_check_in_method') # 获取前端告知的签到方式

            check_in_service = CheckInService.get_instance()

            service_result = check_in_service.perform_check_in(
                user=user, # 当前操作用户
                booking_pk=booking_pk,
                latitude=latitude,
                longitude=longitude,
                photo=photo_file,
                notes=notes,
                client_check_in_method=client_check_in_method # 传入前端告知的签到方式
            )

            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=service_result.data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
        except UnicodeDecodeError as ude:
            # 捕获 UnicodeDecodeError，通常发生在尝试将二进制数据（如图片）解码为文本时
            logger.error(f"UnicodeDecodeError caught during check-in for booking {booking_pk}: {ude}. "
                         f"This often happens with file uploads when raw request body is accessed as text. "
                         f"Request content type: {request.META.get('CONTENT_TYPE')}", exc_info=True)
            # 返回一个 BadRequestException，避免内部服务器错误，并给前端一个明确的提示
            raise BadRequestException(detail="请求数据编码错误，请检查上传文件或请求体格式。", code="unicode_decode_error")
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