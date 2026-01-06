# bookings/service/booking_service.py
from django.db import transaction
from django.utils import timezone
from typing import List, Tuple
from django.db.models import QuerySet

from bookings.models import Booking
from core.service import BaseService, ServiceResult  # 导入 BaseService 和 ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException


# Lazy import models if used only in methods to avoid circular dependency
# from spaces.models import Space, BookableAmenity

class BookingService(BaseService):
    _dao_map = {  # 定义需要注入的 DAO
        'booking_dao': 'booking',
        'violation_dao': 'violation',
    }

    # BaseService 的 __init__ 会自动处理 DAO 的注入

    def get_admin_bookings_queryset(self, user, spaces_loaded: bool) -> QuerySet[Booking]:
        try:
            return self.booking_dao.get_bookings_for_admin_view(user, spaces_loaded)
        except Exception as e:
            # 这里的 get_admin_bookings_queryset 是返回 QuerySet 供 Admin 使用，
            # 不应该返回 ServiceResult。如果真的有异常，应该向上抛出让 Admin 处理。
            # 或者，如果 DAO 已经封装了 none()，直接返回 none() 是更优雅的方式。
            # 这里按照原 Admin 逻辑，直接返回 QuerySet
            return self.booking_dao.get_queryset().none()  # 返回空 QuerySet，错误留待Admin消息提示

    def _check_booking_permission(self, user, booking: Booking, permission_codename: str) -> bool:
        from spaces.models import Space  # 延迟导入
        target_space = self.booking_dao.get_target_space_for_booking(booking)
        return user.is_superuser or user.is_system_admin or \
            (target_space and user.has_perm(permission_codename, target_space))

    @transaction.atomic
    def approve_bookings(self, user, queryset: QuerySet[Booking]) -> ServiceResult[Tuple[int, int]]:
        approved_count = 0
        warnings = []
        errors = []
        for booking in queryset:
            if booking.status == 'PENDING':
                if self._check_booking_permission(user, booking, 'spaces.can_manage_space_bookings') or \
                        user.has_perm('bookings.can_approve_booking'):
                    try:
                        self.booking_dao.update(
                            booking,
                            status='APPROVED',
                            reviewed_by=user,
                            reviewed_at=timezone.now()
                        )
                        approved_count += 1
                    except Exception as e:
                        errors.append(f"批准预订 {booking.id} 失败: {e}")
                else:
                    errors.append(f"您没有权限批准预订 {booking.id}。")
            else:
                warnings.append(f"预订 {booking.id} 状态为 {booking.status}，无法批准。")

        if errors:
            return ServiceResult.error_result(
                message="部分预订操作失败", errors=errors + warnings,
                error_code=BadRequestException.default_code, status_code=BadRequestException.status_code
            )
        if warnings and approved_count == 0:
            return ServiceResult.success_result(
                data=(approved_count, len(warnings)), message="操作完成，但存在警告。", warnings=warnings
            )
        return ServiceResult.success_result(
            data=(approved_count, len(warnings)), message=f"成功批准了 {approved_count} 条预订。", warnings=warnings
        )

    # ... 其他 methods 类似修改 ...
    @transaction.atomic
    def mark_no_show_and_violate(self, user, queryset: QuerySet[Booking]) -> ServiceResult[Tuple[int, int]]:
        no_show_count = 0
        violation_count = 0
        warnings = []
        errors = []
        for booking in queryset:
            if self._check_booking_permission(user, booking, 'spaces.can_manage_space_bookings') or \
                    user.has_perm('bookings.can_check_in_booking'):
                if booking.status in ['PENDING', 'APPROVED'] and booking.end_time < timezone.now():
                    try:
                        self.booking_dao.update(booking, status='NO_SHOW')
                        no_show_count += 1

                        target_space = self.booking_dao.get_target_space_for_booking(booking)
                        space_type_for_violation = target_space.space_type if target_space else None
                        if space_type_for_violation:
                            self.violation_dao.create_violation(
                                user=booking.user,
                                booking=booking,
                                space_type=space_type_for_violation,
                                violation_type='NO_SHOW',
                                description=f"用户 {booking.user.get_full_name} 未在 {getattr(target_space, 'name', '未知空间')} 预订中签到。",
                                issued_by=user,
                                penalty_points=1
                            )
                            violation_count += 1
                        else:
                            warnings.append(f"预订 {booking.id} 无法确定空间类型，未能创建违规记录。")
                    except Exception as e:
                        errors.append(f"标记预订 {booking.id} 为未到场或创建违规记录失败: {e}")
                else:
                    warnings.append(f"预订 {booking.id} 状态为 {booking.status} 或未过期，无法标记为未到场。")
            else:
                errors.append(f"您没有权限对预订 {booking.id} 进行未到场标记或创建违规记录。")

        if errors:
            return ServiceResult.error_result(
                message="部分预订操作失败", errors=errors + warnings,
                error_code=BadRequestException.default_code, status_code=BadRequestException.status_code
            )
        return ServiceResult.success_result(
            data=(no_show_count, violation_count),
            message=f"成功标记 {no_show_count} 条预订为未到场，创建 {violation_count} 条违规记录。",
            warnings=warnings
        )