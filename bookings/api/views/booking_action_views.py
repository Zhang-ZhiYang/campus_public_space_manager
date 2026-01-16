# bookings/api/views/booking_action_views.py
import logging

from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from core.utils.response import success_response, error_response
from core.utils.exceptions import CustomAPIException, InternalServerError, BadRequestException

from bookings.service.booking_service import BookingService
from bookings.service.violation_service import ViolationService
from bookings.models import BOOKING_STATUS_CHOICES, Violation  # For status choices and Violation model




# --- Concrete Booking Action Views ---
class BookingCancelAPIView(APIView):
    pass

class BookingCheckInAPIView(APIView):
    pass

class BookingCheckOutAPIView(APIView):
    permission_classes = [IsAuthenticated]


class BookingMarkNoShowAPIView(APIView):
    permission_classes = [IsAuthenticated]



class BookingApproveAPIView(APIView):
    permission_classes = [IsAuthenticated]



class BookingRejectAPIView(APIView):
    permission_classes = [IsAuthenticated]


# --- Violation Action Views ---
class ViolationMarkResolvedAPIView(APIView):
    permission_classes = [IsAuthenticated]

