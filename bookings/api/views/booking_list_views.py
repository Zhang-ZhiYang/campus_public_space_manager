# bookings/api/views/booking_list_views.py
import logging
import hashlib
import json

from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

from core.pagination import CustomPageNumberPagination
from core.utils.response import success_response
from core.utils.exceptions import CustomAPIException, InternalServerError
from core.utils.constants import MSG_SUCCESS, HTTP_200_OK, MSG_CREATED, \
    HTTP_201_CREATED  # <--- IMPORTED MSG_CREATED, HTTP_201_CREATED
from core.service.cache import CacheService, CachedDictObject
from core.service.factory import ServiceFactory
from core.decorators import is_system_admin_required, is_admin_or_space_manager_required

from bookings.api.serializers import (
    BookingSerializer, ViolationSerializer, BanPolicySerializer,
    DailyBookingLimitSerializer, UserBanSerializer, UserExemptionSerializer,
    ViolationCreateUpdateSerializer, BanPolicyCreateUpdateSerializer,
    DailyBookingLimitCreateUpdateSerializer, UserBanCreateUpdateSerializer, UserExemptionCreateUpdateSerializer
)
from bookings.api.filters import (
    BookingFilter, ViolationFilter, UserBanFilter,
    UserExemptionFilter, DailyBookingLimitFilter, BanPolicyFilter
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
from spaces.models import Space, SpaceType
from django.contrib.auth.models import Group

logger = logging.getLogger(__name__)


def _get_request_query_params_hash(request_query_params):
    if not request_query_params:
        return 'not_specified'
    sorted_params = dict(sorted(request_query_params.items()))
    params_string = json.dumps(sorted_params, sort_keys=True)
    return hashlib.md5(params_string.encode('utf-8')).hexdigest()


class BaseListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    service_class = None
    serializer_class = None
    cache_key_prefix = None
    cache_list_postfix = 'list'

    def get_queryset(self):
        user = self.request.user
        service = self.service_class()

        service_result = service.list_items(user, self.request.query_params)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    def list(self, request, *args, **kwargs):
        try:
            dynamic_cache_kwargs = {'query_params_hash': _get_request_query_params_hash(request.query_params)}

            cached_full_response_data = CacheService.get_list_cache(
                key_prefix=self.cache_key_prefix,
                custom_postfix=self.cache_list_postfix,
                **dynamic_cache_kwargs
            )

            if cached_full_response_data is not None:
                logger.debug(f"View 层缓存命中 {self.cache_key_prefix} 列表数据.")
                return success_response(
                    message=MSG_SUCCESS,
                    data=cached_full_response_data,
                    status_code=HTTP_200_OK
                )

            queryset_unpaginated_filtered = self.filter_queryset(self.get_queryset())
            total_count = queryset_unpaginated_filtered.count()

            page_data = None
            if self.pagination_class:
                self.request.successful_response_status = HTTP_200_OK
                paginator = self.pagination_class()
                page_data = paginator.paginate_queryset(queryset_unpaginated_filtered, self.request, view=self)
            else:
                page_data = list(queryset_unpaginated_filtered)

            serializer = self.get_serializer(page_data, many=True)
            final_serialised_results = serializer.data

            final_response_data_for_cache = None
            if self.pagination_class and paginator:
                final_response_data_for_cache = paginator.get_paginated_response(final_serialised_results).data
            else:
                final_response_data_for_cache = {
                    "count": total_count,
                    "next": None,
                    "previous": None,
                    "results": final_serialised_results
                }

            timeout = CacheService.get_timeout_for_key_prefix(f"{self.cache_key_prefix}:{self.cache_list_postfix}")
            CacheService.set_list_cache(
                key_prefix=self.cache_key_prefix,
                custom_postfix=self.cache_list_postfix,
                value=final_response_data_for_cache,
                timeout=timeout,
                **dynamic_cache_kwargs
            )
            logger.debug(f"View 层缓存 {self.cache_key_prefix} 列表数据.")

            return success_response(
                message=MSG_SUCCESS,
                data=final_response_data_for_cache,
                status_code=HTTP_200_OK
            )
        except CustomAPIException as e:
            logger.warning(f"{self.__class__.__name__} CustomAPIException (list): {e.code} - {e.detail}")
            raise e
        except Exception as e:
            logger.exception(f"{self.__class__.__name__} 列出失败，发生未知错误。")
            raise InternalServerError(detail="服务器内部错误。")


class BookingListAPIView(BaseListView):
    serializer_class = BookingSerializer
    service_class = BookingService
    filterset_class = BookingFilter
    cache_key_prefix = 'bookings:booking'
    cache_list_postfix = 'list_by_user'

    def get_queryset(self):
        user = self.request.user
        service = self.service_class()

        service_result = service.list_bookings(user=user, filters=self.request.query_params)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()


class ViolationListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    serializer_class = ViolationSerializer
    service_class = ViolationService
    filterset_class = ViolationFilter
    cache_key_prefix = 'bookings:violation'
    cache_list_postfix = 'list_all'

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ViolationCreateUpdateSerializer
        return ViolationSerializer

    def get_queryset(self):
        user = self.request.user
        service = self.service_class()
        if user.is_superuser or getattr(user, 'is_system_admin', False) or (
                user.is_staff and user.groups.filter(name='空间管理员').exists()):
            service_result = service.list_items(user=user, filters=self.request.query_params)
        else:
            service_result = service.list_user_violations(user=user, filters=self.request.query_params)

        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    @is_admin_or_space_manager_required
    def create(self, request, *args, **kwargs):
        serializer = ViolationCreateUpdateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        service = self.service_class()

        try:
            service_result = service.save_violation(user=user, violation_data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=MSG_CREATED,  # <--- Used MSG_CREATED
                    data=ViolationSerializer(service_result.data).data,
                    status_code=HTTP_201_CREATED  # <--- Used HTTP_201_CREATED
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"Error creating violation: {e}")
            raise InternalServerError(detail="服务器内部错误。")


class BanPolicyListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    serializer_class = BanPolicySerializer
    service_class = BanPolicyService
    filterset_class = BanPolicyFilter
    cache_key_prefix = 'bookings:ban_policy'
    cache_list_postfix = 'list_all'

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return BanPolicyCreateUpdateSerializer
        return BanPolicySerializer

    def get_queryset(self):
        user = self.request.user
        service = self.service_class()
        service_result = service.list_items(user=user, filters=self.request.query_params)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    @is_system_admin_required
    def create(self, request, *args, **kwargs):
        serializer = BanPolicyCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        service = self.service_class()

        try:
            service_result = service.create_ban_policy(user=user, data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=MSG_CREATED,  # <--- Used MSG_CREATED
                    data=service_result.data,
                    status_code=HTTP_201_CREATED  # <--- Used HTTP_201_CREATED
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"Error creating ban policy: {e}")
            raise InternalServerError(detail="服务器内部错误。")


class DailyBookingLimitListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    serializer_class = DailyBookingLimitSerializer
    service_class = DailyBookingLimitService
    filterset_class = DailyBookingLimitFilter
    cache_key_prefix = 'bookings:daily_limit'
    cache_list_postfix = 'list_all'

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return DailyBookingLimitCreateUpdateSerializer
        return DailyBookingLimitSerializer

    def get_queryset(self):
        user = self.request.user
        service = self.service_class()
        service_result = service.list_items(user=user, filters=self.request.query_params)
        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    @is_system_admin_required
    def create(self, request, *args, **kwargs):
        serializer = DailyBookingLimitCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        service = self.service_class()

        try:
            service_result = service.create_daily_limit(user=user, data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=MSG_CREATED,  # <--- Used MSG_CREATED
                    data=service_result.data,
                    status_code=HTTP_201_CREATED  # <--- Used HTTP_201_CREATED
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"Error creating daily booking limit: {e}")
            raise InternalServerError(detail="服务器内部错误。")


class UserBanListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    serializer_class = UserBanSerializer
    service_class = UserBanService
    filterset_class = UserBanFilter
    cache_key_prefix = 'bookings:user_ban'
    cache_list_postfix = 'list_by_user'

    def get_queryset(self):
        user = self.request.user
        service = self.service_class()
        from bookings.service.common_helpers import CommonBookingHelpers
        if CommonBookingHelpers.is_user_admin_or_manager(user):
            service_result = service.list_items(user=user, filters=self.request.query_params)
        else:
            service_result = service.list_user_bans(user=user, filters=self.request.query_params)

        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    @is_admin_or_space_manager_required
    def create(self, request, *args, **kwargs):
        serializer = UserBanCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        service = self.service_class()

        try:
            service_result = service.create_user_ban(user=user, data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=MSG_CREATED,  # <--- Used MSG_CREATED
                    data=service_result.data,
                    status_code=HTTP_201_CREATED  # <--- Used HTTP_201_CREATED
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"Error creating user ban: {e}")
            raise InternalServerError(detail="服务器内部错误。")


class UserExemptionListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination
    filter_backends = [DjangoFilterBackend]
    serializer_class = UserExemptionSerializer
    service_class = UserExemptionService
    filterset_class = UserExemptionFilter
    cache_key_prefix = 'bookings:user_exemption'
    cache_list_postfix = 'list_by_user'

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return UserExemptionCreateUpdateSerializer
        return UserExemptionSerializer

    def get_queryset(self):
        user = self.request.user
        service = self.service_class()
        from bookings.service.common_helpers import CommonBookingHelpers
        if CommonBookingHelpers.is_user_admin_or_manager(user):
            service_result = service.list_items(user=user, filters=self.request.query_params)
        else:
            service_result = service.list_user_exemptions(user=user, filters=self.request.query_params)

        if service_result.success:
            return service_result.data
        else:
            raise service_result.to_exception()

    @is_admin_or_space_manager_required
    def create(self, request, *args, **kwargs):
        serializer = UserExemptionCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        service = self.service_class()

        try:
            service_result = service.create_exemption(user=user, data=serializer.validated_data)
            if service_result.success:
                return success_response(
                    message=MSG_CREATED,  # <--- Used MSG_CREATED
                    data=service_result.data,
                    status_code=HTTP_201_CREATED  # <--- Used HTTP_201_CREATED
                )
            else:
                raise service_result.to_exception()
        except CustomAPIException as e:
            raise e
        except Exception as e:
            logger.exception(f"Error creating user exemption: {e}")
            raise InternalServerError(detail="服务器内部错误。")