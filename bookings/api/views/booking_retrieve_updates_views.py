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
from core.decorators import is_admin_or_space_manager_required # <<--- 导入装饰器

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
        注意：此处的权限检查在 Service 层完成，允许预订用户本人、系统管理员、空间管理员查看。
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
        except CustomAPIException as e:
            logger.warning(
                f"Known API Exception caught in BookingRetrieveUpdateDestroyAPIView (retrieve):  - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"获取预订详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    @is_admin_or_space_manager_required # <<--- 更新操作需要管理员或空间管理员权限
    def update(self, request, *args, **kwargs):
        """
        更新预订。只允许修改 purpose, expected_attendees 等非核心字段。
        此方法的权限现在由装饰器保证，但细节字段限制仍由 Serializer 内部的 update 方法处理。
        """
        try:
            pk = self.kwargs[self.lookup_field]
            try:
                real_instance = BookingModel.objects.get(pk=pk)
            except BookingModel.DoesNotExist:
                raise NotFoundException(detail="预订记录未找到。")

            user = request.user
            partial = kwargs.pop('partial', False)

            serializer = self.get_serializer(real_instance, data=request.data, partial=partial, context={'request': request})
            serializer.is_valid(raise_exception=True)

            updated_booking_instance = serializer.save()

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

    @is_admin_or_space_manager_required # <<--- 部分更新操作也需要管理员或空间管理员权限
    def partial_update(self, request, *args, **kwargs):
        # 调用 update 方法，并传入 partial=True
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    @is_admin_or_space_manager_required # <<--- 删除操作需要管理员或空间管理员权限
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