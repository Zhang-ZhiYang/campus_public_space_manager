# bookings/api/views/booking_action_views.py
import logging

from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from core.utils.response import success_response, error_response
from core.utils.exceptions import CustomAPIException, InternalServerError, BadRequestException
from core.utils.constants import MSG_SUCCESS, HTTP_200_OK, HTTP_204_NO_CONTENT

from core.decorators import is_system_admin_required, is_admin_or_space_manager_required  # Importing here

from bookings.api.serializers import BookingSerializer, ViolationCreateUpdateSerializer
from bookings.service.booking_service import BookingService
from bookings.service.violation_service import ViolationService
from bookings.models import BOOKING_STATUS_CHOICES, Violation
from users.models import CustomUser

logger = logging.getLogger(__name__)


# --- Helper for action views ---
def _perform_booking_status_action(self, request, pk: int, new_status: str,
                                   permission_check_callback: callable = None,  # 接收一个函数来执行更精细的权限检查
                                   additional_notes: str = ""):
    user = request.user
    booking_service = BookingService()

    try:
        # 在 Service 层进行权限检查，这样可以集中管理权限逻辑
        # 简化版：这里只是作为参数传递，实际 Service 内部会判断
        service_result = booking_service.update_booking_status(
            booking_id=pk,
            new_status=new_status,
            admin_user=user,
            admin_notes=f"{additional_notes} (由 {user.username} 操作于 {timezone.now().isoformat()})"
            # permission_check_callback=permission_check_callback # 如果 Service 需要外部回调，可在此传递
        )

        if service_result.success:
            # 简化：ServiceResult.data 预计是 dict
            return success_response(
                message=service_result.message,
                data=BookingSerializer(service_result.data).data,
                status_code=service_result.status_code
            )
        else:
            raise service_result.to_exception()
    except CustomAPIException as e:
        logger.warning(f"{self.__class__.__name__} CustomAPIException: {e.code} - {e.detail}")
        raise e
    except Exception as e:
        logger.exception(f"{self.__class__.__name__} unhandled error for booking {pk} to status {new_status}: {e}")
        raise InternalServerError(detail="服务器内部错误。")


# --- Concrete Booking Action Views ---
class BookingCancelAPIView(APIView):
    permission_classes = [IsAuthenticated]  # 认证用户可取消，Service 内将判断是否是自身预订或管理员

    def post(self, request, pk, *args, **kwargs):
        return _perform_booking_status_action(self, request, pk, 'CANCELLED', None, "预订被取消。")


class BookingCheckInAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @is_admin_or_space_manager_required  # 需要管理员/空间管理权限
    def post(self, request, pk, *args, **kwargs):
        return _perform_booking_status_action(self, request, pk, 'CHECKED_IN', None, "预订已签到。")


class BookingCheckOutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @is_admin_or_space_manager_required  # 需要管理员/空间管理权限
    def post(self, request, pk, *args, **kwargs):
        return _perform_booking_status_action(self, request, pk, 'CHECKED_OUT', None, "预订已签出。")


class BookingMarkNoShowAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @is_admin_or_space_manager_required  # 需要管理员/空间管理权限
    def post(self, request, pk, *args, **kwargs):
        return _perform_booking_status_action(self, request, pk, 'NO_SHOW', None, "预订被标记为未到场并已产生违约。")


class BookingApproveAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @is_admin_or_space_manager_required  # 需要管理员/空间管理权限
    def post(self, request, pk, *args, **kwargs):
        return _perform_booking_status_action(self, request, pk, 'APPROVED', None, "预订已批准。")


class BookingRejectAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @is_admin_or_space_manager_required  # 需要管理员/空间管理权限
    def post(self, request, pk, *args, **kwargs):
        return _perform_booking_status_action(self, request, pk, 'REJECTED', None, "预订已拒绝。")


# --- Violation Action Views ---
class ViolationMarkResolvedAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @is_admin_or_space_manager_required  # 需要管理员/空间管理权限
    def post(self, request, *args, **kwargs):
        pk_list = request.data.get('violation_pks')
        if not pk_list or not isinstance(pk_list, list):
            raise BadRequestException(detail="请求体中必须包含 `violation_pks` 列表。", code="invalid_payload")

        user = request.user
        violation_service = ViolationService()

        try:
            service_result = violation_service.mark_violations_resolved(user=user, pk_list=pk_list)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=service_result.data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            logger.warning(f"ViolationMarkResolvedAPIView CustomAPIException: {e.code} - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"ViolationMarkResolvedAPIView unhandled error: {e}")
            raise InternalServerError(detail="服务器内部错误。")