# bookings/api/views/booking_retrieve_updates_views.py
import logging
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError as DRFValidationError # 导入 DRF 的 ValidationError

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError, BadRequestException, NotFoundException
from core.service.cache import CachedDictObject  # 用于处理 Service 返回的字典数据

from bookings.models import Booking as BookingModel  # 导入模型，供 CachedDictObject 使用
from bookings.api.serializers import BookingDetailSerializer
from bookings.service.booking_service import BookingService  # 导入 BookingService
# from core.decorators import is_admin_or_space_manager_required # <<--- 移除此导入，因为不再直接用于 update/partial_update 方法的权限

logger = logging.getLogger(__name__)

class BookingRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    预订详情的获取、更新和取消（逻辑删除）API 视图。
    """
    permission_classes = [IsAuthenticated] # <<--- 必须保留 IsAuthenticated
    serializer_class = BookingDetailSerializer  # 默认Serializer
    lookup_field = 'pk'

    def get_object(self):
        """
        通过 BookingService 获取预订对象，并进行权限检查。
        如果 Service 返回的是字典，使用 CachedDictObject 封装。
        注意：Service 层会进行权限检查，允许预订用户本人、系统管理员、空间管理员查看。
        """
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        booking_service = BookingService()
        service_result = booking_service.get_booking(user, pk)

        if service_result.success:
            # 如果 service.get_booking 返回的是字典，需要封装。
            # 这是为了让序列化器可以像访问模型实例一样访问属性。
            if isinstance(service_result.data, dict):
                return CachedDictObject(service_result.data, model_class=BookingModel)
            return service_result.data # 如果是真实的模型实例，直接返回
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        """
        获取单个预订详情。权限由 get_object 和 Service 层处理。
        """
        try:
            instance = self.get_object()  # get_object 已经处理了权限和数据获取
            serializer = self.get_serializer(instance)
            return success_response(
                message="成功获取预订详情。",
                data=serializer.data,
                status_code=status.HTTP_200_OK
            )
        except CustomAPIException as e: # 捕获 ServiceResult 转换来的业务异常
            logger.warning(
                f"Known API Exception caught in BookingRetrieveUpdateDestroyAPIView (retrieve):  - {e.detail}")
            raise e
        except Exception as e: # 捕获其他未知异常
            logger.exception(f"获取预订详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    # @is_admin_or_space_manager_required # <<--- 移除此装饰器，权限将由 serializer.update 内部处理
    def update(self, request, *args, **kwargs):
        """
        更新预订。权限控制位于 serializer.update 方法内部。
        """
        try:
            pk = self.kwargs[self.lookup_field]
            try:
                # 获取真实的模型实例用于更新 (因为 serializer.save() 需要接收一个模型实例)
                real_instance = BookingModel.objects.get(pk=pk)
            except BookingModel.DoesNotExist:
                raise NotFoundException(detail="预订记录未找到。")

            user = request.user
            partial = kwargs.pop('partial', False)

            serializer = self.get_serializer(real_instance, data=request.data, partial=partial, context={'request': request})
            serializer.is_valid(raise_exception=True) # 验证失败会直接抛出 DRFValidationError 或 CustomAPIException (如果serializer抛出)

            updated_booking_instance = serializer.save() # 调用 serializer 的 update 方法

            # 序列化刚刚更新的模型实例来构建响应数据
            response_data = BookingDetailSerializer(updated_booking_instance, context={'request': request}).data

            return success_response(
                message="预订更新成功。",
                data=response_data,
                status_code=status.HTTP_200_OK
            )
        except DRFValidationError as e: # 明确捕获 DRF 的 ValidationError
            logger.warning(
                f"Validation Error caught in BookingRetrieveUpdateDestroyAPIView (update) for booking {pk}: {e.detail}")
            raise e # 重新抛出，DRF 的 exception handler 会将其转换为 400 Bad Request
        except CustomAPIException as e: # 捕获 ServiceResult 转换来的业务异常，包括 ForbiddenException
            logger.warning(
                f"Known API Exception caught in BookingRetrieveUpdateDestroyAPIView (update) for booking {pk}: - {e.detail}")
            raise e
        except Exception as e: # 捕获其他未知异常
            logger.exception(f"更新预订失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    # @is_admin_or_space_manager_required # <<--- 移除此装饰器
    def partial_update(self, request, *args, **kwargs):
        # 调用 update 方法，并传入 partial=True
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    # <<--- 保持此装饰器，仅管理员/空间管理员能通过此接口删除
    from core.decorators import is_admin_or_space_manager_required # 重新导入
    @is_admin_or_space_manager_required
    def destroy(self, request, *args, **kwargs):
        """
        取消预订（逻辑删除）。
        此方法的权限现在由装饰器保证，但Service层也会进行二次校验（允许用户本人取消）。
        """
        user = request.user
        pk = self.kwargs[self.lookup_field]

        reason = request.data.get('reason', '管理员/空间管理员取消') # 默认给一个管理员取消的原因
        if not reason: # 强制提供原因
            raise BadRequestException(detail="取消预订必须提供原因。", code="missing_cancel_reason")

        try:
            booking_service = BookingService()
            service_result = booking_service.cancel_booking(user, pk, reason)

            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=None,
                    status_code=status.HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in BookingRetrieveUpdateDestroyAPIView (destroy):  - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"取消预订失败 (ID: {pk}, User: {user.username})。")
            raise InternalServerError(detail="服务器内部错误。")