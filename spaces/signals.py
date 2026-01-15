# spaces/signals.py (完整修订版，完善级联缓存失效触发逻辑)
import logging
from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group  # 确保导入 Group
from guardian.shortcuts import assign_perm, remove_perm

from core.service.cache import CacheService
# 从 spaces.tasks 导入所有的 Celery 缓存失效任务
from spaces.tasks import (
    invalidate_amenity_cache,
    invalidate_space_cache,
    invalidate_bookable_amenity_cache,
    invalidate_spacetype_cache,
    # invalidate_space_cache_for_manager, # REMOVED: 移除此任务的导入
    invalidate_all_spaces_dependent_on_spacetype,
    invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity,
    # NEW: 导入新的批量任务
    invalidate_all_spaces_dependent_on_group,  # NEW: 导入 Group 相关的失效任务
)

# 从 .models 导入模型和权限常量、辅助函数
from .models import (
    Space, BookableAmenity, Amenity, SpaceType,
    get_all_descendant_spaces,
    SPACE_MANAGEMENT_PERMISSIONS,
    SPACE_VIEW_ONLY_PERMISSIONS,
    BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS
)

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


# ====================================================================
# SpaceType 模型的信号处理
# ====================================================================
@receiver(post_save, sender=SpaceType)
def spacetype_post_save_handler(sender, instance, **kwargs):
    """
    当 SpaceType 实例保存 (创建或更新) 后，异步触发缓存失效任务。
    除了自身缓存，还需要失效所有依赖于此 SpaceType 的 Space 对象的缓存。
    """
    invalidate_spacetype_cache.delay(instance.pk)
    logger.debug(
        f"Post_save signal for SpaceType {instance.name} (PK:{instance.pk}). Triggered cache invalidation task.")

    # 触发所有依赖于此 SpaceType 的 Space 对象的缓存失效
    # 因为 SpaceType 的改变可能影响到所有关联 Space 的 is_bookable 属性、默认预订规则等。
    invalidate_all_spaces_dependent_on_spacetype.delay(instance.pk)
    logger.debug(f"Triggered invalidate_all_spaces_dependent_on_spacetype for SpaceType (PK:{instance.pk}).")


@receiver(post_delete, sender=SpaceType)
def spacetype_post_delete_handler(sender, instance, **kwargs):
    """
    当 SpaceType 实例删除后，异步触发缓存失效任务。
    """
    invalidate_spacetype_cache.delay(instance.pk)
    logger.debug(
        f"Post_delete signal for SpaceType {instance.name} (PK:{instance.pk}). Triggered cache invalidation task.")

    # 当 SpaceType 被删除，所有之前引用它的 Space 的 `space_type` 字段会变为 NULL。
    # 这会影响这些 Space 的有效预订规则。`invalidate_all_spaces_dependent_on_spacetype` 任务
    # 可以通过清理通用 Space 列表来间接处理这类变化。
    invalidate_all_spaces_dependent_on_spacetype.delay(instance.pk)
    logger.debug(
        f"Triggered invalidate_all_spaces_dependent_on_spacetype (for deletion) for SpaceType (PK:{instance.pk}).")


# ====================================================================
# Amenity 模型的信号处理
# ====================================================================
@receiver(post_save, sender=Amenity)
def amenity_post_save_handler(sender, instance, **kwargs):
    """
    当 Amenity 实例保存 (创建或更新) 后，异步触发缓存失效任务。
    除了自身缓存，还需要失效所有引用它的 BookableAmenity 及其所属 Space 的缓存。
    """
    invalidate_amenity_cache.delay(instance.pk)
    logger.debug(f"Post_save signal for Amenity {instance.name} (PK:{instance.pk}). Triggered cache invalidation task.")

    # 触发所有引用此 Amenity 的 BookableAmenity 及其所属 Space 的缓存失效。
    # 因为 Amenity 的 name/description/is_bookable_individually 改变会影响 BookableAmenity 的 to_dict()
    # 进而影响 Space 的 to_dict() (因为 BookableAmenity 是 Space 的内联)
    invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity.delay(instance.pk)
    logger.debug(
        f"Triggered invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity for Amenity (PK:{instance.pk}).")


@receiver(post_delete, sender=Amenity)
def amenity_post_delete_handler(sender, instance, **kwargs):
    """
    当 Amenity 实例删除后，异步触发缓存失效任务。
    """
    invalidate_amenity_cache.delay(instance.pk)
    logger.debug(
        f"Post_delete signal for Amenity {instance.name} (PK:{instance.pk}). Triggered cache invalidation task.")

    # 触发所有引用此 Amenity (即使已被删除) 的 BookableAmenity 及其所属 Space 的缓存失效。
    # 这里的关键是 BookableAmenity 的 amenity 字段会变为 None，这会影响其序列化和 Space 的序列化。
    invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity.delay(instance.pk)
    logger.debug(
        f"Triggered invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity (for deletion) for Amenity (PK:{instance.pk}).")


# ====================================================================
# BookableAmenity 模型的信号处理
# ====================================================================
@receiver(post_save, sender=BookableAmenity)
def bookable_amenity_post_save_handler(sender, instance, created, **kwargs):
    """
    当 BookableAmenity 被创建或更新时，如果其所属 Space 有 managed_by，则为其分配对象级权限，
    并异步触发缓存失效任务。
    """
    # 权限分配逻辑 (保持不变)
    if instance.space and instance.space.managed_by:
        manager_of_space = instance.space.managed_by
        logger.debug(
            f"Handling post_save for BookableAmenity {instance.id}, created: {created}. Manager of space {instance.space.name}: {manager_of_space.username}.")

        for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
            try:
                assign_perm(f'spaces.{perm_codename}', manager_of_space, instance)
                logger.debug(
                    f"Assigned 'spaces.{perm_codename}' to {manager_of_space.username} for BookableAmenity {instance.id} in space {instance.space.name}.")
            except Exception as e:
                logger.error(
                    f"Failed to assign 'spaces.{perm_codename}' to {manager_of_space.username} for BookableAmenity {instance.id}: {e}")
    else:
        logger.debug(
            f"BookableAmenity {instance.id} has no associated space manager. No permissions assigned via this amenity.")

    # 异步触发缓存失效
    space_pk = instance.space_id
    invalidate_bookable_amenity_cache.delay(instance.pk, space_pk)
    logger.debug(f"Post_save signal for BookableAmenity (PK:{instance.pk}). Triggered cache invalidation task.")

    # 因为 BookableAmenity 的变化直接影响所属 Space 的详情和所有 Space 列表，
    # Space 详情的缓存失效由 invalidate_bookable_amenity_cache 任务内部处理。
    # 对于所有 Space 列表，我们在这里触发全局失效 (而不是特定的 Space 列表)，
    # 因为 BookableAmenity 的变化可能影响到 Space 列表的过滤或显示。
    # CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all') # 已经 moved into invalidate_bookable_amenity_cache
    # CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered') # already moved into invalidate_bookable_amenity_cache
    # logger.debug(f"Triggered global Space list cache invalidation due to BookableAmenity change for PK:{instance.pk}.")


@receiver(post_delete, sender=BookableAmenity)
def bookable_amenity_post_delete_handler(sender, instance, **kwargs):
    """
    当 BookableAmenity 实例删除后，异步触发缓存失效任务。
    同时，也要失效其所属 Space 的缓存。
    """
    space_pk = instance.space_id
    invalidate_bookable_amenity_cache.delay(instance.pk, space_pk)
    logger.debug(f"Post_delete signal for BookableAmenity (PK:{instance.pk}). Triggered cache invalidation task.")

    # 同理，删除 BookableAmenity 也会影响所属 Space 的详情和列表
    # CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all') # already moved into invalidate_bookable_amenity_cache
    # CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered') # already moved into invalidate_bookable_amenity_cache
    # logger.debug(f"Triggered global Space list cache invalidation due to BookableAmenity deletion for PK:{instance.pk}.")


# ====================================================================
# Space 模型的信号处理
# ====================================================================
@receiver(pre_save, sender=Space)
def store_old_managed_by_and_parent_for_space(sender, instance, **kwargs):
    """
    在 Space 实例保存之前，存储其旧的 managed_by 值、parent_space.pk 和 space_type.pk，
    以便在 post_save 中比较和撤销权限及清除缓存。
    """
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            instance._old_managed_by = old_instance.managed_by
            instance._old_parent_space_pk = old_instance.parent_space.pk if old_instance.parent_space else None
            instance._old_space_type_pk = old_instance.space_type.pk if old_instance.space_type else None  # 新增
            # 存储旧的 permitted_groups PKs，用于 M2M 字段变化检测
            instance._old_permitted_groups_pks = set(old_instance.permitted_groups.values_list('pk', flat=True))

            logger.debug(
                f"Pre_save for Space {instance.name}, old managed_by stored: {old_instance.managed_by}, old_parent_pk: {instance._old_parent_space_pk}, old_space_type_pk: {instance._old_space_type_pk}, old_permitted_groups_pks: {instance._old_permitted_groups_pks}"
            )
        except sender.DoesNotExist:
            logger.warning(
                f"Space with PK {instance.pk} not found in pre_save; treating as new instance for _old_managed_by, _old_parent_space_pk, _old_space_type_pk, _old_permitted_groups_pks."
            )
            instance._old_managed_by = None
            instance._old_parent_space_pk = None
            instance._old_space_type_pk = None
            instance._old_permitted_groups_pks = set()  # For new instances, it's an empty set
    else:
        instance._old_managed_by = None
        instance._old_parent_space_pk = None
        instance._old_space_type_pk = None
        instance._old_permitted_groups_pks = set()  # For new instances, it's an empty set


@receiver(post_save, sender=Space)
def assign_space_management_permissions_and_invalidate_cache(sender, instance, created, **kwargs):
    """
    处理 Space 实例保存后的权限分配逻辑和缓存失效任务。
    """
    logger.debug(
        f"Handling post_save for Space {instance.name}, PK={instance.pk}, created: {created}. Current Manager: {instance.managed_by}"
    )

    old_managed_by = getattr(instance, '_old_managed_by', None)
    current_managed_by = instance.managed_by
    old_parent_pk = getattr(instance, '_old_parent_space_pk', None)
    current_parent_pk = instance.parent_space.pk if instance.parent_space else None

    old_space_type_pk = getattr(instance, '_old_space_type_pk', None)
    current_space_type_pk = instance.space_type.pk if instance.space_type else None

    # 获取 current permitted_groups 的 PKs
    current_permitted_groups_pks = set(instance.permitted_groups.values_list('pk', flat=True))
    old_permitted_groups_pks = getattr(instance, '_old_permitted_groups_pks', set())

    # --- 1. 权限分配逻辑 (保持现有逻辑) ---
    # 撤销旧管理人员的权限
    if old_managed_by and old_managed_by != current_managed_by:
        logger.info(
            f"Revoking direct permissions for old manager {old_managed_by.username} (PK:{old_managed_by.pk}) on space {instance.name}.")
        all_relevant_perms = set(SPACE_MANAGEMENT_PERMISSIONS + SPACE_VIEW_ONLY_PERMISSIONS)
        for perm_codename in all_relevant_perms:
            try:
                remove_perm(f'spaces.{perm_codename}', old_managed_by, instance)
            except Exception as e:
                logger.warning(
                    f"Failed to revoke 'spaces.{perm_codename}' from {old_managed_by.username} for Space {instance.name}: {e}")

        for ba in instance.bookable_amenities.all():
            for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
                try:
                    remove_perm(f'spaces.{perm_codename}', old_managed_by, ba)
                except Exception as e:
                    logger.warning(
                        f"Failed to revoke 'spaces.{perm_codename}' from {old_managed_by.username} for BookableAmenity {ba.id} in space {instance.name}: {e}")

    # 授予新管理人员权限
    if current_managed_by:
        logger.info(
            f"Assigning permissions for new manager {current_managed_by.username} (PK:{current_managed_by.pk}) on space {instance.name}.")

        space_manager_group, created_group = Group.objects.get_or_create(name='空间管理员')
        if not current_managed_by.groups.filter(name='空间管理员').exists():
            current_managed_by.groups.add(space_manager_group)
            logger.info(
                f"User {current_managed_by.username} added to '空间管理员' group as they manage space {instance.name}.")

        for perm_codename in SPACE_MANAGEMENT_PERMISSIONS:
            try:
                assign_perm(f'spaces.{perm_codename}', current_managed_by, instance)
            except Exception as e:
                logger.error(
                    f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for direct Space {instance.name}: {e}")

        for ba in instance.bookable_amenities.all().iterator():
            for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
                try:
                    assign_perm(f'spaces.{perm_codename}', current_managed_by, ba)
                except Exception as e:
                    logger.error(
                        f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for BookableAmenity {ba.id} in space {instance.name}: {e}")

        parent_space_traversal = instance.parent_space
        processed_parent_pks = set()
        while parent_space_traversal:
            if parent_space_traversal.pk in processed_parent_pks:
                logger.error(
                    f"Circular parent_space reference detected for Space {instance.name} starting from {parent_space_traversal.name}. Stopping parent permission assignment to prevent infinite loop.")
                break
            processed_parent_pks.add(parent_space_traversal.pk)

            for perm_codename in SPACE_VIEW_ONLY_PERMISSIONS:
                try:
                    assign_perm(f'spaces.{perm_codename}', current_managed_by, parent_space_traversal)
                except Exception as e:
                    logger.error(
                        f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for parent Space {parent_space_traversal.name}: {e}")

            parent_space_traversal = parent_space_traversal.parent_space

        descendant_spaces = get_all_descendant_spaces(instance)
        for child_space in descendant_spaces:
            if child_space.managed_by is None or child_space.managed_by == current_managed_by:
                for perm_codename in SPACE_MANAGEMENT_PERMISSIONS:
                    try:
                        assign_perm(f'spaces.{perm_codename}', current_managed_by, child_space)
                    except Exception as e:
                        logger.error(
                            f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for child Space {child_space.name}: {e}")

                for ba_child in child_space.bookable_amenities.all().iterator():
                    if ba_child.space.managed_by is None or ba_child.space.managed_by == current_managed_by:
                        for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
                            try:
                                assign_perm(f'spaces.{perm_codename}', current_managed_by, ba_child)
                            except Exception as e:
                                logger.error(
                                    f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for BookableAmenity {ba_child.id} in child space {child_space.name}: {e}")
                    else:
                        logger.info(
                            f"Skipping BookableAmenity permission assignment for {ba_child.id} in child space {child_space.name} "
                            f"from parent manager {current_managed_by.username} (PK:{current_managed_by.pk}), due to child space having a different manager ({ba_child.space.managed_by.username}, PK:{ba_child.space.managed_by.pk}).")
            else:
                logger.info(
                    f"Skipping top-down MANAGEMENT permission assignment for child space {child_space.name} (PK:{child_space.pk}) "
                    f"from parent manager {current_managed_by.username} (PK:{current_managed_by.pk}), as child space is directly managed by a DIFFERENT manager ({child_space.managed_by.username}, PK:{child_space.managed_by.pk}).")
    else:
        logger.info(
            f"Space {instance.name} has no manager (managed_by is None). No permissions assigned to any user via this space instance.")

    # --- 2. 缓存失效逻辑 ---
    affected_parent_pks = set()
    if old_parent_pk:
        affected_parent_pks.add(old_parent_pk)
    if current_parent_pk:  # 新的父空间可能不同或被新分配
        affected_parent_pks.add(current_parent_pk)

    # 如果 managed_by 发生变化，需要清除所有 Space 的通用列表缓存
    # 因为 managed_by 的变化可能影响用户能看到的 Space 列表。
    if old_managed_by and (old_managed_by != current_managed_by) or \
            current_managed_by and (old_managed_by != current_managed_by or created):
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
        logger.info(
            f"Triggered global Space list cache invalidation due to managed_by change for Space (PK:{instance.pk}).")

    # 如果 space_type 发生变化 (或从无到有，或从有到无，或从一个类型到另一个类型)
    if old_space_type_pk != current_space_type_pk:
        # 触发所有依赖此 SpaceType 的 Space 的缓存失效（包括列表和详情）
        # 这里会清除所有 Space 的列表，所以再次在这里清除不需要
        if old_space_type_pk:
            invalidate_all_spaces_dependent_on_spacetype.delay(old_space_type_pk)
        if current_space_type_pk:
            invalidate_all_spaces_dependent_on_spacetype.delay(current_space_type_pk)
        logger.info(
            f"Triggered dependent Space cache invalidation due to space_type change for Space (PK:{instance.pk}).")

    # 如果 permitted_groups 发生变化，也需要清除所有 Space 的通用列表缓存
    # 因为 permitted_groups 的变化可能影响用户能看到的 Space 列表 (即使是公共列表， permitted_groups_display 也会变)
    if old_permitted_groups_pks != current_permitted_groups_pks:
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
        logger.info(
            f"Triggered global Space list cache invalidation due to permitted_groups change for Space (PK:{instance.pk}).")

    # 触发核心 Space 缓存失效任务 (处理详情、list_by_parent 和 bookable_amenity 列表)
    invalidate_space_cache.delay(instance.pk, list(affected_parent_pks))
    logger.debug(f"Post_save signal for Space (PK:{instance.pk}). Triggered cache invalidation task.")


@receiver(post_delete, sender=Space)
def space_post_delete_handler(sender, instance, **kwargs):
    """
    当 Space 实例删除后，异步触发缓存失效任务。
    """
    # 获取被删除空间的父空间PKs，用于失效其父空间的子空间列表缓存
    affected_parent_pks = [instance.parent_space_id] if instance.parent_space_id else []

    # 如果此空间有管理者，清除所有 Space 的通用列表缓存
    # 因为删除一个空间会影响该管理者能看到的列表。
    if instance.managed_by_id:
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
        logger.info(
            f"Triggered global Space list cache invalidation for manager {instance.managed_by_id} due to Space (PK:{instance.pk}) deletion.")

    # 如果此空间关联了 SpaceType，触发所有依赖此 SpaceType 的 Space 的缓存失效
    if instance.space_type_id:
        invalidate_all_spaces_dependent_on_spacetype.delay(instance.space_type_id)
        logger.info(
            f"Triggered dependent Space cache invalidation due to Space (PK:{instance.pk}) deletion affecting SpaceType {instance.space_type_id}.")

    # 如果此空间有 permitted_groups，清除所有 Space 的通用列表缓存
    # 因为删除一个空间会影响这些组能看到的列表。
    # (注意: M2M 关系在删除 Space 实例时会自动断开，但 for loop in pre_delete 才能获取旧关系)
    # 可以在 pre_delete 中获取 instance.permitted_groups.all()
    # 鉴于此，更简单的做法是直接全局清除列表缓存，反正上面也清过了。
    # new changes to capture _old_permitted_groups_pks in pre_delete could be added for more precision

    # 触发核心 Space 缓存失效任务 (处理详情、list_by_parent 和 bookable_amenity 列表)
    invalidate_space_cache.delay(instance.pk, affected_parent_pks)
    logger.debug(f"Post_delete signal for Space (PK:{instance.pk}). Triggered cache invalidation task.")


# ====================================================================
# NEW: Group 模型的信号处理
# ====================================================================
@receiver(post_save, sender=Group)
@receiver(post_delete, sender=Group)
def group_change_handler(sender, instance, **kwargs):
    """
    当 Group 实例保存 (创建/更新) 或删除后，触发所有可能依赖此 Group 的 Space 列表缓存失效。
    因为 Space.permitted_groups_display 依赖 Group 名称，所以 Group 变化会影响 Space 的显示。
    """
    logger.info(
        f"Group '{instance.name}' (PK:{instance.pk}) changed/deleted. Triggering dependent Space cache invalidation.")

    # 当 Group 信息（名称等）发生变化时，可能影响所有显示 `permitted_groups_display` 的 Space。
    # 最安全且高效的方式是全局清除所有 Space 的列表缓存。
    invalidate_all_spaces_dependent_on_group.delay(instance.pk)  # 调用新的 Celery 任务