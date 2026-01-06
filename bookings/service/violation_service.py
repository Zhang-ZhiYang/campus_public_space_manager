# bookings/service/violation_service.py
from django.db import transaction
from django.utils import timezone
from typing import List, Tuple
from django.db.models import QuerySet

from bookings.models import Violation
from core.service import BaseService, ServiceResult
from core.utils.exceptions import ForbiddenException, BadRequestException, NotFoundException


# Lazy import Space
# from spaces.models import Space, SpaceType

class ViolationService(BaseService):
    _dao_map = {
        'violation_dao': 'violation',
    }

    def get_admin_violations_queryset(self, user) -> QuerySet[Violation]:
        return self.violation_dao.get_violations_for_admin_view(user)

    def can_manage_violation(self, user, violation: Violation) -> bool:
        if user.is_superuser or user.is_system_admin:
            return True

        from spaces.models import Space  # 延迟导入
        # 使用 DAO 中的辅助方法
        managed_spacetypes = self.violation_dao.get_managed_spacetypes_by_user(user)

        if violation.space_type:
            return managed_spacetypes.filter(id=violation.space_type.id).exists()
        else:  # Global violation (space_type is None)
            return user.is_superuser or user.is_system_admin

    @transaction.atomic
    def save_violation(self, user, violation_obj: Violation, form_changed_data: List[str]) -> ServiceResult[Violation]:
        try:
            # The logic from save_model in ViolationAdmin
            if not violation_obj.space_type and violation_obj.booking:
                if violation_obj.booking.space and violation_obj.booking.space.space_type:
                    violation_obj.space_type = violation_obj.booking.space.space_type
                elif violation_obj.booking.bookable_amenity and violation_obj.booking.bookable_amenity.space \
                        and violation_obj.booking.bookable_amenity.space.space_type:
                    violation_obj.space_type = violation_obj.bookable_amenity.space.space_type

            if not self.can_manage_violation(user, violation_obj):
                raise ForbiddenException(f"您没有权限修改此违规记录(ID: {violation_obj.pk})。")

            if 'is_resolved' in form_changed_data:
                if violation_obj.is_resolved and not violation_obj.resolved_at:
                    violation_obj.resolved_at = timezone.now()
                    violation_obj.resolved_by = user
                elif not violation_obj.is_resolved and violation_obj.resolved_at:
                    violation_obj.resolved_at = None
                    violation_obj.resolved_by = None

            # 使用 DAO 更新对象
            updated_violation = self.violation_dao.update(violation_obj, **{
                k: getattr(violation_obj, k) for k in violation_obj.__dict__ if
                k not in ['_state', '_prefetched_objects_cache']  # 提取所有更新过的字段或相关字段
            })
            return ServiceResult.success_result(data=updated_violation, message="违规记录保存成功。")
        except Exception as e:
            return self._handle_exception(e, default_message="保存违规记录失败")

    @transaction.atomic
    def mark_violations_resolved(self, user, queryset: QuerySet[Violation]) -> ServiceResult[Tuple[int, int]]:
        resolved_count = 0
        warnings = []
        errors = []
        for violation in queryset:
            try:
                if self.can_manage_violation(user, violation):
                    if not violation.is_resolved:
                        self.violation_dao.update(
                            violation,
                            is_resolved=True,
                            resolved_by=user,
                            resolved_at=timezone.now()
                        )
                        resolved_count += 1
                    else:
                        warnings.append(f"违规 {violation.id} 已是解决状态。")
                else:
                    errors.append(f"您没有权限解决违规 {violation.id}。")
            except Exception as e:
                errors.append(f"解决违规 {violation.id} 失败: {e}")

        if errors:
            return ServiceResult.error_result(
                message="部分违规记录解决失败", errors=errors + warnings,
                error_code=BadRequestException.default_code, status_code=BadRequestException.status_code
            )
        return ServiceResult.success_result(
            data=(resolved_count, len(warnings)),
            message=f"成功解决了 {resolved_count} 条违约记录。", warnings=warnings
        )