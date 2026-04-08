# check_in/api/views.py
import logging

from rest_framework.parsers import MultiPartParser, FormParser, JSONParser # <-- 导入 JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from core.utils.response import success_response
from core.utils.constants import HTTP_200_OK, HTTP_201_CREATED, HTTP_403_FORBIDDEN

from check_in.api.serializers import QRCheckInSerializer, CheckInRecordSerializer
from check_in.service.check_in_service import CheckInService
from core.utils.exceptions import CustomAPIException, InternalServerError, ForbiddenException

from bookings.models import Booking

logger = logging.getLogger(__name__)

class CheckInAPIView(APIView):
    """
    签到接口：允许用户自行签到，或工作人员（系统管理员、空间管理员、签到员）代签。
    支持拍照、扫码、定位多种签到方式。
    - POST /api/v1/check-in/bookings/<int:booking_pk>/
    """
    permission_classes = [IsAuthenticated]
    # 修改此处，添加 JSONParser
    parser_classes = [JSONParser, MultiPartParser, FormParser] # <-- 确保 JSONParser 在前面，优先处理JSON

    def post(self, request, booking_pk: int):
        """
        执行签到操作。
        根据请求用户和预订信息，智能判断是否为工作人员代签。
        """
        serializer = QRCheckInSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        validated_data = serializer.validated_data

        photo_file = validated_data.get('photo')
        latitude = validated_data.get('latitude')
        longitude = validated_data.get('longitude')
        notes = validated_data.get('notes', '')

        try:
            check_in_service = CheckInService.get_instance()

            # --- START DYNAMIC is_staff_manual_check_in logic ---
            is_staff_manual_check_in_param = False # 默认初始化为 False

            try:
                booking_for_lookup = Booking.objects.select_related(
                    'user',
                    'space',
                    'bookable_amenity__space'
                ).get(pk=booking_pk)

                target_space = booking_for_lookup.related_space
                if not target_space and booking_for_lookup.bookable_amenity:
                    target_space = booking_for_lookup.bookable_amenity.space

                if user.pk != booking_for_lookup.user.pk and target_space:
                    is_current_user_staff_with_perm = (
                        user.is_system_admin or
                        user.is_space_manager or
                        (user.is_check_in_staff and user.has_perm('spaces.can_check_in_real_space', target_space))
                    )

                    if is_current_user_staff_with_perm:
                        is_staff_manual_check_in_param = True
                        logger.debug(
                            f"User {user.username} (PK:{user.pk}) identified as staff attempting to check-in for Booking {booking_pk} (owner: {booking_for_lookup.user.username}). Setting is_staff_manual_check_in_param to True.")
                    else:
                        logger.debug(
                            f"User {user.username} (PK:{user.pk}) is not owner and not authorized staff for Booking {booking_pk}.")
                else:
                    logger.debug(f"User {user.username} (PK:{user.pk}) is booking owner OR booking {booking_pk} has no associated space to perform staff check for.")

            except Booking.DoesNotExist:
                logger.debug(f"Booking {booking_pk} not found during initial lookup in CheckInAPIView.")
            except Exception as e:
                logger.exception(
                    f"Error during initial booking/space lookup for staff check-in decision in CheckInAPIView (Booking PK: {booking_pk}). Error: {e}")
            # --- END DYNAMIC is_staff_manual_check_in logic ---

            service_result = check_in_service.perform_check_in(
                user=user,
                booking_pk=booking_pk,
                latitude=latitude,
                longitude=longitude,
                photo=photo_file,
                notes=notes,
                is_staff_manual_check_in=is_staff_manual_check_in_param
            )

            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=service_result.data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
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