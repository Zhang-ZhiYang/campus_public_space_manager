# check_in/api/views.py
import logging

from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from core.utils.response import success_response
from core.utils.constants import HTTP_200_OK, HTTP_201_CREATED, HTTP_403_FORBIDDEN

from check_in.api.serializers import QRCheckInSerializer, CheckInRecordSerializer
from check_in.service.check_in_service import CheckInService
from core.utils.exceptions import CustomAPIException, InternalServerError, ForbiddenException
# from core.decorators import is_staff_can_operate_for_qs_obj # NEW: 这个装饰器在此方案中可能不再直接使用，因为它更适合限定整个视图或方法，而不是用于动态参数判断。先注释掉。

from bookings.models import Booking # NEW: 导入Booking模型，用于获取预订用户
# from spaces.models import Space # NEW: 导入Space模型，用于对象级权限判断，但可以放到service层面获取，避免重复查询

logger = logging.getLogger(__name__)

class CheckInAPIView(APIView):
    """
    签到接口：允许用户自行签到，或工作人员（系统管理员、空间管理员、签到员）代签。
    支持拍照、扫码、定位多种签到方式。
    - POST /api/v1/check-in/bookings/<int:booking_pk>/
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser] # 支持文件上传 (用于签到图片)

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
                # 预获取预订对象。这里只进行查询，不验证预订状态，让 Service 层全面处理。
                # 重要的是要获取 booking.user 来判断是否为本人签到。
                # select_related 优化：获取相关联的用户和可能存在的空间信息。
                booking_for_lookup = Booking.objects.select_related(
                    'user',
                    'space',
                    'bookable_amenity__space'
                ).get(pk=booking_pk)

                # 获取与此预订相关的"最终"空间，用于权限检查
                target_space = booking_for_lookup.related_space
                # 如果是设施预订，则通过设施获取空间
                if not target_space and booking_for_lookup.bookable_amenity:
                    target_space = booking_for_lookup.bookable_amenity.space

                # 如果当前用户不是预订人本人 (user.pk != booking_for_lookup.user.pk)
                # 并且目标空间存在 (才能进行对象级权限判断)
                if user.pk != booking_for_lookup.user.pk and target_space:
                    # 检查当前用户是否是具有签到权限的“工作人员”
                    # (user.is_system_admin 或 user.is_space_manager 或
                    # (user.is_check_in_staff 且 user 对 target_space 拥有 'can_check_in_real_space' 权限))
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
                # 如果预订不存在，让 service.perform_check_in 去处理 NotFoundException
                logger.debug(f"Booking {booking_pk} not found during initial lookup in CheckInAPIView.")
            except Exception as e:
                # 捕获其他潜在错误，记录但不阻止 service 调用，让 service.perform_check_in 处理
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
                is_staff_manual_check_in=is_staff_manual_check_in_param # 动态设置
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

# Removed StaffCheckInAPIView as it's no longer needed.

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