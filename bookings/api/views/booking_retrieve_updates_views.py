# bookings/api/views/booking_retrieve_updates_views.py
import logging
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError, BadRequestException, NotFoundException
from core.service.cache import CachedDictObject  # 用于处理 Service 返回的字典数据

from bookings.models import Booking as BookingModel  # 导入模型，供 CachedDictObject 使用
from bookings.api.serializers import BookingDetailSerializer
from bookings.service.booking_service import BookingService  # 导入 BookingService

logger = logging.getLogger(__name__)


class BookingRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    预订详情的获取、更新和取消（逻辑删除）API 视图。
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BookingDetailSerializer  # 默认Serializer
    lookup_field = 'pk'

    def get_object(self):
        """
        通过 BookingService 获取预订对象，并进行权限检查。
        如果 Service 返回的是字典，使用 CachedDictObject 封装。
        """
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        booking_service = BookingService()
        service_result = booking_service.get_booking(user, pk)

        if service_result.success:
            # 如果 service.get_booking 返回的是字典，需要封装
            return CachedDictObject(service_result.data, model_class=BookingModel)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        """
        获取单个预订详情。
        """
        try:
            instance = self.get_object()  # get_object 已经处理了权限和数据获取
            serializer = self.get_serializer(instance)
            return success_response(
                message="成功获取预订详情。",
                data=serializer.data,
                status_code=status.HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in BookingRetrieveUpdateDestroyAPIView (retrieve):  - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"获取预订详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    def update(self, request, *args, **kwargs):
        """
        更新预订。只允许修改 purpose, expected_attendees 等非核心字段。
        """
        try:
            # get_object() 返回的是 CachedDictObject，不能直接用于 DRF 的 update()。
            # 需要从数据库重新获取实际的模型实例。
            pk = self.kwargs[self.lookup_field]
            try:
                # 重新从数据库获取实例，因为 DRF 的 update 需要真正的模型实例
                real_instance = BookingModel.objects.get(pk=pk)
            except BookingModel.DoesNotExist:
                raise NotFoundException(detail="预订记录未找到。")

            user = request.user
            partial = kwargs.pop('partial', False)  # True for PATCH, False for PUT

            serializer = self.get_serializer(real_instance, data=request.data, partial=partial)
            serializer.is_valid(raise_exception=True)

            # 在 serializer.save() 内部，BookingDetailSerializer 的 update 方法会执行字段检查
            updated_booking_instance = serializer.save()

            # 再次通过 serializer 渲染返回数据
            response_data = BookingDetailSerializer(updated_booking_instance, context={'request': request}).data

            return success_response(
                message="预订更新成功。",
                data=response_data,
                status_code=status.HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in BookingRetrieveUpdateDestroyAPIView (update):  - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"更新预订失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    def partial_update(self, request, *args, **kwargs):
        # 调用 update 方法，并传入 partial=True
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """
        取消预订（逻辑删除）。
        """
        user = request.user
        pk = self.kwargs[self.lookup_field]

        # 预订取消需要提供原因
        reason = request.data.get('reason', '用户主动取消')
        if not reason:
            raise BadRequestException(detail="取消预订必须提供原因。", code="missing_cancel_reason")

        try:
            booking_service = BookingService()
            service_result = booking_service.cancel_booking(user, pk, reason)

            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=None,  # 取消操作通常不返回数据
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