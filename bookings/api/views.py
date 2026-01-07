# bookings/api/views.py
from rest_framework.views import APIView
from rest_framework.response import Response as DRFResponse  # 避免和我们自定义的 response 模块中的 Response 混淆
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
import logging

# 导入 Django 的 ValidationError，以便在视图层捕获模型验证错误
from django.core.exceptions import ValidationError as DjangoValidationError

from bookings.service.booking_service import BookingService
from core.utils.response import success_response, error_response  # 从 core.utils.response 导入统一响应函数
# 导入自定义异常，以供异常处理分支使用
from core.utils.exceptions import CustomAPIException, ServiceException, BadRequestException, NotFoundException, \
    ForbiddenException

logger = logging.getLogger(__name__)


class BookingCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    booking_service = BookingService()  # 实例化服务

    def post(self, request, *args, **kwargs):
        user = request.user
        data = request.data  # POST 请求数据

        space_id = data.get('space_id')
        bookable_amenity_id = data.get('bookable_amenity_id')

        # 确保至少一个被提供
        if not (space_id or bookable_amenity_id):
            return error_response(
                message="预订请求不完整。",
                error={"code": BadRequestException.default_code, "detail": "预订必须指定一个空间ID或设施ID。"},
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 提取其他预订相关数据
        booking_details = {
            'start_time': data.get('start_time'),
            'end_time': data.get('end_time'),
            'purpose': data.get('purpose', ''),
            'booked_quantity': data.get('booked_quantity', 1),
            'space_id': space_id,
            'bookable_amenity_id': bookable_amenity_id,
        }

        try:
            # 调用 BookingService 创建预订，ServiceResult 会封装所有业务逻辑和验证结果
            service_result = self.booking_service.create_booking(user, booking_details)

            if service_result.success:
                # 预订成功，返回统一的成功响应格式
                return success_response(
                    message=service_result.message,
                    data={"booking_id": service_result.data.id},
                    status_code=service_result.status_code  # 通常是 201 Created
                )
            else:
                # 业务逻辑错误 (如权限不足, 资源不存在等)，ServiceResult 已经包含了错误信息和状态码
                return error_response(
                    message=service_result.message,
                    error={"code": service_result.error_code,
                           "detail": service_result.errors or [service_result.message]},  # 确保 detail 是一个列表
                    status_code=service_result.status_code
                )

        # 捕获自定义的API异常（如果Service层直接抛出）
        except CustomAPIException as e:
            logger.warning(f"CustomAPIException caught in BookingCreateAPIView: {e.code} - {e.detail}")
            return error_response(
                message=str(e.detail),  # e.detail 可能是字符串或字典，我们取 str()
                error={"code": e.code, "detail": e.detail},
                status_code=e.status_code
            )
        # 捕获我们自定义的 ServiceException（如果Service层没有封装成ServiceResult而是直接抛出）
        except ServiceException as e:
            logger.error(f"ServiceException caught in BookingCreateAPIView: {e.error_code} - {e.message}",
                         exc_info=True)
            return error_response(
                message=e.message,
                error={"code": e.error_code, "detail": e.errors or [str(e)]},
                status_code=e.status_code
            )
        # 捕获 Django 的模型验证错误（例如 DAO 层直接保存时触发 full_clean 失败）
        except DjangoValidationError as e:
            logger.warning(f"DjangoValidationError caught in BookingCreateAPIView: {e}")
            errors_detail = {}
            if hasattr(e, 'error_dict'):  # 字段验证错误
                errors_detail = {field: [str(err) for err in msgs] for field, msgs in e.error_dict.items()}
            elif hasattr(e, 'message_dict'):  # 非字段错误或表单错误
                errors_detail = {field: [str(err) for err in msgs] for field, msgs in e.message_dict.items()}
            else:  # 单个验证消息
                errors_detail = {"non_field_errors": [str(e)]}

            return error_response(
                message="数据验证失败。",
                error={"code": BadRequestException.default_code, "detail": errors_detail},
                status_code=status.HTTP_400_BAD_REQUEST
            )
        # 兜底捕获所有其他未预料到或未明确处理的 Python 异常
        except Exception as e:
            logger.exception("An unhandled exception occurred during booking creation in API view.")

            # 对于内部服务器错误，通常返回一个通用消息，避免泄露内部实现细节
            return error_response(
                message="服务器内部错误。",
                error={"code": "server_error", "detail": str(e)},  # 包含详细的错误栈用于日志，但给客户端返回通用错误码
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )