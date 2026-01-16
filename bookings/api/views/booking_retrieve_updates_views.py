# bookings/api/views/booking_retrieve_updates_views.py
import logging

from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated

from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError, NotFoundException
from core.utils.constants import MSG_SUCCESS, HTTP_200_OK, HTTP_204_NO_CONTENT
from core.service.cache import CachedDictObject
from core.decorators import is_system_admin_required, is_admin_or_space_manager_required

from bookings.api.serializers import (
    BookingSerializer, BookingUpdateSerializer,
    ViolationSerializer, ViolationCreateUpdateSerializer,
    BanPolicySerializer, BanPolicyCreateUpdateSerializer,
    DailyBookingLimitSerializer, DailyBookingLimitCreateUpdateSerializer,
    UserBanSerializer, UserBanCreateUpdateSerializer,
    UserExemptionSerializer, UserExemptionCreateUpdateSerializer
)
from bookings.service.booking_service import BookingService
from bookings.service.violation_service import ViolationService
from bookings.service.ban_policy_service import BanPolicyService
from bookings.service.daily_booking_limit_service import DailyBookingLimitService
from bookings.service.user_ban_service import UserBanService
from bookings.service.user_exemption_service import UserExemptionService

from bookings.models import Booking, Violation, UserSpaceTypeBan, UserSpaceTypeExemption, DailyBookingLimit, \
    SpaceTypeBanPolicy
from users.models import CustomUser

logger = logging.getLogger(__name__)


class BaseRetrieveUpdateDestroyView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'
    service_class = None
    serializer_class = None  # For retrieve
    update_serializer_class = None  # For update
    cache_key_prefix = None
    model_class = None  # For CachedDictObject

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            if self.update_serializer_class:
                return self.update_serializer_class
            return self.serializer_class  # Fallback if no specific update serializer
        return self.serializer_class

    def get_object(self):
        user = self.request.user
        pk = self.kwargs[self.lookup_field]
        service = self.service_class()

        service_result = service.get_by_id(user, pk)
        if service_result.success:
            return CachedDictObject(service_result.data, model_class=self.model_class)
        else:
            raise service_result.to_exception()

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return success_response(
                message=MSG_SUCCESS,
                data=serializer.data,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"{self.__class__.__name__} CustomAPIException (retrieve): {e.code} - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"{self.__class__.__name__} 获取详情失败 (ID: {self.kwargs[self.lookup_field]})。")
            raise InternalServerError(detail="服务器内部错误。")

    def update(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()  # This is CachedDictObject
        pk_from_url = _instance_for_pk.pk

        try:
            real_instance = self.model_class.objects.get(pk=pk_from_url)
        except self.model_class.DoesNotExist:
            raise NotFoundException(detail=f"{self.model_class.__name__} (ID:{pk_from_url}) 未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        service = self.service_class()

        try:
            service_result = service.update_item(user=user, pk=pk_from_url, data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=self.serializer_class(service_result.data).data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            logger.warning(f"{self.__class__.__name__} CustomAPIException (update): {e.code} - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"{self.__class__.__name__} 更新失败 (ID: {pk_from_url})。")
            raise InternalServerError(detail="服务器内部错误。")

    def destroy(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()  # This is CachedDictObject
        pk_from_url = _instance_for_pk.pk

        user = request.user
        service = self.service_class()

        try:
            service_result = service.delete_item(user=user, pk=pk_from_url)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            logger.warning(f"{self.__class__.__name__} CustomAPIException (delete): {e.code} - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"{self.__class__.__name__} 删除失败 (ID: {pk_from_url})。")
            raise InternalServerError(detail="服务器内部错误。")


# --- Concrete Booking Retrieve/Update/Destroy Views ---
class BookingRetrieveUpdateDestroyAPIView(BaseRetrieveUpdateDestroyView):
    serializer_class = BookingSerializer
    update_serializer_class = BookingUpdateSerializer
    service_class = BookingService
    cache_key_prefix = 'bookings:booking'
    model_class = Booking

    # User can retrieve their own booking, or admin can retrieve any.
    # Update/Delete can be done by user or admin/manager. Permissions handled by service.
    def update(self, request, *args, **kwargs):
        # Service handles specific fields user can update (e.g. purpose) vs admin (status, notes)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # Service handles permissions (user can cancel self, admin can delete any)
        return super().destroy(request, *args, **kwargs)


# --- Concrete Violation Retrieve/Update/Destroy Views ---
class ViolationRetrieveUpdateDestroyAPIView(BaseRetrieveUpdateDestroyView):
    # Added @is_admin_or_space_manager_required to update/destroy methods
    permission_classes = [IsAuthenticated]
    serializer_class = ViolationSerializer
    update_serializer_class = ViolationCreateUpdateSerializer
    service_class = ViolationService
    cache_key_prefix = 'bookings:violation'
    model_class = Violation

    @is_admin_or_space_manager_required  # Only admin/manager can update
    def update(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            real_instance = self.model_class.objects.get(pk=pk_from_url)
        except self.model_class.DoesNotExist:
            raise NotFoundException(detail=f"{self.model_class.__name__} (ID:{pk_from_url}) 未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        service = self.service_class()

        try:
            service_result = service.save_violation(user=user,
                                                    violation_data={'id': pk_from_url, **serializer.validated_data})
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=ViolationSerializer(service_result.data).data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新违规记录失败 (ID: {pk_from_url})。")
            raise InternalServerError("服务器内部错误。")

    @is_admin_or_space_manager_required  # Only admin/manager can destroy
    def destroy(self, request, *args, **kwargs):
        user = request.user
        pk_from_url = self.kwargs[self.lookup_field]
        service = ViolationService()

        try:
            service_result = service.delete_item(user=user, pk=pk_from_url)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"删除违规记录失败 (ID: {pk_from_url})。")
            raise InternalServerError("服务器内部错误。")


# --- Concrete BanPolicy Retrieve/Update/Destroy Views ---
class BanPolicyRetrieveUpdateDestroyAPIView(BaseRetrieveUpdateDestroyView):
    permission_classes = [IsAuthenticated]
    serializer_class = BanPolicySerializer
    update_serializer_class = BanPolicyCreateUpdateSerializer
    service_class = BanPolicyService
    cache_key_prefix = 'bookings:ban_policy'
    model_class = SpaceTypeBanPolicy

    @is_system_admin_required  # Only system admin can update
    def update(self, request, *args, **kwargs):
        # Service's update_item expects `user` parameter
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            real_instance = self.model_class.objects.get(pk=pk_from_url)
        except self.model_class.DoesNotExist:
            raise NotFoundException(detail=f"{self.model_class.__name__} (ID:{pk_from_url}) 未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        service = self.service_class()

        try:
            service_result = service.update_item(user=user, pk=pk_from_url, data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=self.serializer_class(service_result.data).data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新禁用策略失败 (ID:{pk_from_url}): {e}")
            raise InternalServerError("服务器内部错误。")

    @is_system_admin_required  # Only system admin can destroy
    def destroy(self, request, *args, **kwargs):
        user = request.user
        pk_from_url = self.kwargs[self.lookup_field]
        service = BanPolicyService()

        try:
            service_result = service.delete_item(user=user, pk=pk_from_url)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"删除禁用策略失败 (ID:{pk_from_url}): {e}")
            raise InternalServerError("服务器内部错误。")


# --- Concrete DailyBookingLimit Retrieve/Update/Destroy Views ---
class DailyBookingLimitRetrieveUpdateDestroyAPIView(BaseRetrieveUpdateDestroyView):
    permission_classes = [IsAuthenticated]
    serializer_class = DailyBookingLimitSerializer
    update_serializer_class = DailyBookingLimitCreateUpdateSerializer
    service_class = DailyBookingLimitService
    cache_key_prefix = 'bookings:daily_limit'
    model_class = DailyBookingLimit

    @is_system_admin_required
    def update(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            real_instance = self.model_class.objects.get(pk=pk_from_url)
        except self.model_class.DoesNotExist:
            raise NotFoundException(detail=f"{self.model_class.__name__} (ID:{pk_from_url}) 未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        service = self.service_class()

        try:
            service_result = service.update_item(user=user, pk=pk_from_url, data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=self.serializer_class(service_result.data).data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新每日预订限制失败 (ID:{pk_from_url}): {e}")
            raise InternalServerError("服务器内部错误。")

    @is_system_admin_required
    def destroy(self, request, *args, **kwargs):
        user = request.user
        pk_from_url = self.kwargs[self.lookup_field]
        service = DailyBookingLimitService()

        try:
            service_result = service.delete_item(user=user, pk=pk_from_url)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"删除每日预订限制失败 (ID:{pk_from_url}): {e}")
            raise InternalServerError("服务器内部错误。")


# --- Concrete UserBan Retrieve/Update/Destroy Views ---
class UserBanRetrieveUpdateDestroyAPIView(BaseRetrieveUpdateDestroyView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserBanSerializer
    update_serializer_class = UserBanCreateUpdateSerializer
    service_class = UserBanService
    cache_key_prefix = 'bookings:user_ban'
    model_class = UserSpaceTypeBan

    @is_admin_or_space_manager_required
    def update(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            real_instance = self.model_class.objects.get(pk=pk_from_url)
        except self.model_class.DoesNotExist:
            raise NotFoundException(detail=f"{self.model_class.__name__} (ID:{pk_from_url}) 未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        service = self.service_class()

        try:
            service_result = service.update_item(user=user, pk=pk_from_url, data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=self.serializer_class(service_result.data).data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新用户禁用记录失败 (ID:{pk_from_url}): {e}")
            raise InternalServerError("服务器内部错误。")

    @is_admin_or_space_manager_required
    def destroy(self, request, *args, **kwargs):
        user = request.user
        pk_from_url = self.kwargs[self.lookup_field]
        service = UserBanService()

        try:
            service_result = service.delete_item(user=user, pk=pk_from_url)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"删除用户禁用记录失败 (ID:{pk_from_url}): {e}")
            raise InternalServerError("服务器内部错误。")


# --- Concrete UserExemption Retrieve/Update/Destroy Views ---
class UserExemptionRetrieveUpdateDestroyAPIView(BaseRetrieveUpdateDestroyView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserExemptionSerializer
    update_serializer_class = UserExemptionCreateUpdateSerializer
    service_class = UserExemptionService
    cache_key_prefix = 'bookings:user_exemption'
    model_class = UserSpaceTypeExemption

    @is_admin_or_space_manager_required
    def update(self, request, *args, **kwargs):
        _instance_for_pk = self.get_object()
        pk_from_url = _instance_for_pk.pk

        try:
            real_instance = self.model_class.objects.get(pk=pk_from_url)
        except self.model_class.DoesNotExist:
            raise NotFoundException(detail=f"{self.model_class.__name__} (ID:{pk_from_url}) 未找到。")

        partial = kwargs.get('partial', False)
        serializer = self.get_serializer(real_instance, data=request.data, partial=partial,
                                         context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        service = self.service_class()

        try:
            service_result = service.update_item(user=user, pk=pk_from_url, data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=self.serializer_class(service_result.data).data,
                    status_code=service_result.status_code
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"更新用户豁免记录失败 (ID:{pk_from_url}): {e}")
            raise InternalServerError("服务器内部错误。")

    @is_admin_or_space_manager_required
    def destroy(self, request, *args, **kwargs):
        user = request.user
        pk_from_url = self.kwargs[self.lookup_field]
        service = UserExemptionService()

        try:
            service_result = service.delete_item(user=user, pk=pk_from_url)
            if service_result.success:
                return success_response(
                    message=service_result.message,
                    data=None,
                    status_code=HTTP_204_NO_CONTENT
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"删除用户豁免记录失败 (ID:{pk_from_url}): {e}")
            raise InternalServerError("服务器内部错误。")