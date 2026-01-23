# bookings/tasks/violation_tasks.py
import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from core.service.factory import ServiceFactory
from bookings.models import UserPenaltyPointsPerSpaceType, Violation, UserSpaceTypeBan, SpaceType  # 导入所有需要的模型

logger = logging.getLogger(__name__)

# 使用全局变量缓存 Service 实例，避免在每次任务触发时重复创建
_violation_service_instance = None


def get_violation_service_instance():
    """惰性加载 ViolationService 实例。"""
    global _violation_service_instance
    if _violation_service_instance is None:
        _violation_service_instance = ServiceFactory.get_service('ViolationService')
    return _violation_service_instance

@shared_task(bind=True, max_retries=3, default_retry_delay=60 * 5)  # 5分钟重试间隔
def recalculate_all_penalty_points_and_apply_bans_task(self):
    """
    Celery 定时任务：每天午夜重新计算所有用户的违约点数，并评估/应用禁用策略。
    这用于捕获那些因为时间流逝，或未通过实时信号触发但实际需要重新计算的情况。
    """
    logger.info(
        f"Celery Beat Task (ID:{self.request.id}): Starting recalculation of all penalty points and application of ban policies.")

    violation_service = get_violation_service_instance()

    try:
        # 获取所有有违约记录的用户
        users_with_violations = Violation.objects.values_list('user', flat=True).distinct()

        # 获取所有有惩罚点数记录的用户及其相关空间类型
        users_with_penalty_records = UserPenaltyPointsPerSpaceType.objects.values('user', 'space_type').distinct()

        # 遍历所有可能受影响的用户和空间类型组合
        processed_combinations = set()

        for user_pk in users_with_violations:
            user = Violation.objects.filter(user__pk=user_pk).first().user  # 获取 CustomUser 实例

            # 首先处理该用户的全局点数 (space_type=None)
            if (user_pk, None) not in processed_combinations:
                logger.debug(f"Processing global penalty points for user {user.pk}.")
                violation_service.recalculate_and_apply_ban_policies_for_user_and_space_type(user=user, space_type=None)
                processed_combinations.add((user_pk, None))

            # 其次处理该用户的所有特定空间类型点数
            space_types_for_user = Violation.objects.filter(user__pk=user_pk, space_type__isnull=False).values_list(
                'space_type', flat=True).distinct()
            for space_type_pk in space_types_for_user:
                if (user_pk, space_type_pk) not in processed_combinations:
                    space_type = SpaceType.objects.get(pk=space_type_pk)
                    logger.debug(f"Processing penalty points for user {user.pk} in space type {space_type.pk}.")
                    violation_service.recalculate_and_apply_ban_policies_for_user_and_space_type(user=user,
                                                                                                 space_type=space_type)
                    processed_combinations.add((user_pk, space_type_pk))

        # 确保也处理那些有惩罚点数记录但当前没有活跃违规的用户 (点数可能已清零，需要解除禁用)
        for record in users_with_penalty_records:
            user_pk = record['user']
            space_type_pk = record['space_type']

            if (user_pk, space_type_pk) not in processed_combinations:
                user = UserPenaltyPointsPerSpaceType.objects.filter(user__pk=user_pk).first().user
                space_type = SpaceType.objects.get(pk=space_type_pk) if space_type_pk else None
                logger.debug(
                    f"Processing existing penalty record for user {user.pk} in space type {space_type.pk if space_type else 'Global'}.")
                violation_service.recalculate_and_apply_ban_policies_for_user_and_space_type(user=user,
                                                                                             space_type=space_type)
                processed_combinations.add((user_pk, space_type_pk))

        logger.info(
            f"Celery Beat Task (ID:{self.request.id}): All penalty points recalculated and ban policies applied successfully for {len(processed_combinations)} combinations.")

    except Exception as e:
        logger.exception(
            f"Error in recalculate_all_penalty_points_and_apply_bans_task (ID:{self.request.id}). Retrying...")
        self.retry(exc=e)