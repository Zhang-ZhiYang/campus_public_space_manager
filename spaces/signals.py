# spaces/signals.py (完整修订版，包含 Space is_active 级联更新和细化的缓存失效逻辑)
import logging
from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group  # 确保导入 Group
from guardian.shortcuts import assign_perm, remove_perm
from django.db import transaction  # 用于原子操作
from django.db.models import Q  # 用于复杂查询，特别是status更新

from core.service.cache import CacheService
# 从 spaces.tasks 导入所有的 Celery 缓存失效任务 (Tasks 文件不变)
from spaces.tasks import (
    invalidate_amenity_cache,
    invalidate_space_cache,
    invalidate_bookable_amenity_cache,
    invalidate_spacetype_cache,
    invalidate_all_spaces_dependent_on_spacetype,
    invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity,
    invalidate_all_spaces_dependent_on_group,
)

# 从 .models 导入模型和权限常量、辅助函数
from .models import (
    Space, BookableAmenity, Amenity, SpaceType,
    get_all_descendant_spaces,
    SPACE_MANAGEMENT_PERMISSIONS,
    SPACE_VIEW_ONLY_PERMISSIONS,
    # BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS # 不再用于信号中的对象级权限分配，可以从此处移除以避免混淆
)

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


# ====================================================================
# SpaceType 模型的信号处理 (保持不变)
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

    invalidate_all_spaces_dependent_on_spacetype.delay(instance.pk)
    logger.debug(
        f"Triggered invalidate_all_spaces_dependent_on_spacetype (for deletion) for SpaceType (PK:{instance.pk}).")


# ====================================================================
# Amenity 模型的信号处理 (保持不变)
# ====================================================================
@receiver(post_save, sender=Amenity)
def amenity_post_save_handler(sender, instance, **kwargs):
    """
    当 Amenity 实例保存 (创建或更新) 后，异步触发缓存失效任务。
    除了自身缓存，还需要失效所有引用它的 BookableAmenity 及其所属 Space 的缓存。
    """
    invalidate_amenity_cache.delay(instance.pk)
    logger.debug(f"Post_save signal for Amenity {instance.name} (PK:{instance.pk}). Triggered cache invalidation task.")

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

    invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity.delay(instance.pk)
    logger.debug(
        f"Triggered invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity (for deletion) for Amenity (PK:{instance.pk}).")


# ====================================================================
# BookableAmenity 模型的信号处理 (移除对象级权限分配，仅处理缓存更新)
# ====================================================================
@receiver(post_save, sender=BookableAmenity)
def bookable_amenity_post_save_handler(sender, instance, created, **kwargs):
    """
    当 BookableAmenity 被创建或更新时，异步触发缓存失效任务。
    此处理器不再进行对象级权限分配给 SpaceManager，因为这一职责现已由 Space 模型的权限统一管理。
    """
    logger.debug(f"Handling post_save for BookableAmenity {instance.id}, created: {created}.")
    # --- REMOVED PERMISSION ASSIGNMENT LOGIC ---
    # if instance.space and instance.space.managed_by:
    #     manager_of_space = instance.space.managed_by
    #     logger.debug(
    #         f"Handling post_save for BookableAmenity {instance.id}, created: {created}. Manager of space {instance.space.name}: {manager_of_space.username}.")
    #
    #     for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
    #         try:
    #             assign_perm(f'spaces.{perm_codename}', manager_of_space, instance)
    #             logger.debug(
    #                 f"Assigned 'spaces.{perm_codename}' to {manager_of_space.username} for BookableAmenity {instance.id} in space {instance.space.name}.")
    #         except Exception as e:
    #             logger.error(
    #                 f"Failed to assign 'spaces.{perm_codename}' to {manager_of_space.username} for BookableAmenity {instance.id}: {e}")
    # else:
    #     logger.debug(
    #         f"BookableAmenity {instance.id} has no associated space manager. No permissions assigned via this amenity.")
    # --- END REMOVED PERMISSION ASSIGNMENT LOGIC ---

    # 异步触发缓存失效
    space_pk = instance.space_id
    invalidate_bookable_amenity_cache.delay(instance.pk, space_pk)
    logger.debug(f"Post_save signal for BookableAmenity (PK:{instance.pk}). Triggered cache invalidation task.")


@receiver(post_delete, sender=BookableAmenity)
def bookable_amenity_post_delete_handler(sender, instance, **kwargs):
    """
    当 BookableAmenity 实例删除后，异步触发缓存失效任务。
    同时，也要失效其所属 Space 的缓存。
    """
    space_pk = instance.space_id
    invalidate_bookable_amenity_cache.delay(instance.pk, space_pk)
    logger.debug(f"Post_delete signal for BookableAmenity (PK:{instance.pk}). Triggered cache invalidation task.")


# ====================================================================
# Space 模型的信号处理
# ====================================================================

# 统一所有 pre_save 逻辑到一个函数中
@receiver(pre_save, sender=Space)
def space_pre_save_handler(sender, instance, **kwargs):
    """
    在 Space 实例保存之前，存储其旧的 is_active、managed_by、parent_space.pk、space_type.pk
    和 permitted_groups PKs 等，以便在 post_save 中比较和处理。
    """
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            instance._old_is_active = old_instance.is_active  # <--- NEW: 存储旧的 is_active
            instance._old_managed_by = old_instance.managed_by
            instance._old_parent_space_pk = old_instance.parent_space.pk if old_instance.parent_space else None
            instance._old_space_type_pk = old_instance.space_type.pk if old_instance.space_type else None
            instance._old_permitted_groups_pks = set(old_instance.permitted_groups.values_list('pk', flat=True))

            logger.debug(
                f"Pre_save for Space {instance.name} (PK:{instance.pk}): "
                f"old_is_active={instance._old_is_active}, "
                f"old_managed_by={old_instance.managed_by}, "
                f"old_parent_pk={instance._old_parent_space_pk}, "
                f"old_space_type_pk={instance._old_space_type_pk}, "
                f"old_permitted_groups_pks={instance._old_permitted_groups_pks}"
            )
        except sender.DoesNotExist:
            logger.warning(
                f"Space with PK {instance.pk} not found in pre_save; treating as new instance for _old_values."
            )
            instance._old_is_active = None
            instance._old_managed_by = None
            instance._old_parent_space_pk = None
            instance._old_space_type_pk = None
            instance._old_permitted_groups_pks = set()
    else:  # 新实例
        instance._old_is_active = None
        instance._old_managed_by = None
        instance._old_parent_space_pk = None
        instance._old_space_type_pk = None
        instance._old_permitted_groups_pks = set()


@receiver(post_save, sender=Space)
def space_post_save_handler(sender, instance, created, **kwargs):
    """
    处理 Space 实例保存后的权限分配逻辑和缓存失效任务。
    新增：如果 Space 被设置为不启用，其下的所有 BookableAmenity 也被设置为不启用和不可预订。
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
    old_is_active = getattr(instance, '_old_is_active', False)  # 获取 pre_save 中存储的旧值，默认 False 以防 None 导致错误

    current_permitted_groups_pks = set(instance.permitted_groups.values_list('pk', flat=True))
    old_permitted_groups_pks = getattr(instance, '_old_permitted_groups_pks', set())

    # --- Step 1: 处理 BookableAmenity 实例的级联状态更新 ---
    # 仅当 Space 的 is_active 从 True 变为 False 时触发，或者新创建的空间默认就是 inactive
    # 确保只在状态确实发生变化时才执行
    if instance.is_active is False and (old_is_active is True or (created and old_is_active is None)):
        if created and old_is_active is None:  # for a newly created inactive space
            logger.info(f"新创建的空间 {instance.name} (PK:{instance.pk}) 默认设置为不启用。更新所有关联的空间设施实例。")
        else:  # for an existing space becoming inactive
            logger.info(f"空间 {instance.name} (PK:{instance.pk}) 被设置为不启用。更新所有关联的空间设施实例。")

        # 找到所有与此空间关联的、当前仍活跃或可预订的 BookableAmenity 实例
        # 使用 Q 对象确保我们只更新那些需要改变状态的实例
        affected_bookable_amenities_qs = BookableAmenity.objects.filter(
            space=instance
        ).filter(Q(is_active=True) | Q(is_bookable=True))

        # 在更新前收集它们的 PKs 用于缓存失效
        affected_ba_pks = list(affected_bookable_amenities_qs.values_list('pk', flat=True))

        if affected_bookable_amenities_qs.exists():
            with transaction.atomic():
                # 执行批量更新，将它们设置为不活跃且不可预订。
                actual_updated_count = affected_bookable_amenities_qs.update(is_active=False, is_bookable=False)
                logger.info(f"将空间 {instance.name} 下的 {actual_updated_count} 个设施实例设置为不启用和不可预订。")

            # 为每个受影响的 BookableAmenity 触发缓存失效任务
            # invalidate_bookable_amenity_cache 任务会同时失效 BookableAmenity 自身、其所属空间以及其父级空间的相关列表缓存。
            for ba_pk in affected_ba_pks:
                invalidate_bookable_amenity_cache.delay(ba_pk, instance.pk)
                logger.debug(f"已触发 BookableAmenity (PK:{ba_pk}) 的缓存失效，因为空间不活跃。")
        else:
            logger.debug(f"在不活跃的空间 {instance.name} (PK:{instance.pk}) 下未找到需要更新的活跃或可预订设施实例。")

    # --- Step 2: 权限分配逻辑 (保持现有逻辑，但移除 BookableAmenity 的对象级权限分配) ---
    # 撤销旧管理人员的权限
    if old_managed_by and old_managed_by != current_managed_by:
        logger.info(
            f"正在撤销旧管理员 {old_managed_by.username} (PK:{old_managed_by.pk}) 在空间 {instance.name} 上的直接权限。")
        # 仅撤销 Space 相关的权限。BookableAmenity 权限不再在此信号中为 SpaceManager 直接分配。
        all_relevant_space_perms = set(SPACE_MANAGEMENT_PERMISSIONS + ['can_delete_space'])
        for perm_codename in all_relevant_space_perms:
            try:
                remove_perm(f'spaces.{perm_codename}', old_managed_by, instance)
            except Exception as e:
                logger.warning(
                    f"从 {old_managed_by.username} 撤销 'spaces.{perm_codename}' 在空间 {instance.name} 上时失败: {e}")

        # --- REMOVED: BookableAmenity object-level permission revocation for old manager ---
        # for ba in instance.bookable_amenities.all():
        #     for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
        #         try:
        #             remove_perm(f'spaces.{perm_codename}', old_managed_by, ba)
        #         except Exception as e:
        #             logger.warning(
        #                 f"Failed to revoke 'spaces.{perm_codename}' from {old_managed_by.username} for BookableAmenity {ba.id} in space {instance.name}: {e}")
        # --- END REMOVED ---

    # 授予新管理人员权限
    if current_managed_by:
        logger.info(
            f"正在为新管理员 {current_managed_by.username} (PK:{current_managed_by.pk}) 分配在空间 {instance.name} 上的权限。")

        space_manager_group, created_group = Group.objects.get_or_create(name='空间管理员')
        if not current_managed_by.groups.filter(name='空间管理员').exists():
            current_managed_by.groups.add(space_manager_group)
            logger.info(
                f"用户 {current_managed_by.username} 已被添加到 '空间管理员' 分组，因为TA管理空间 {instance.name}。")

        # 为直接空间分配 Space 相关的管理权限
        direct_space_perms_to_assign = SPACE_MANAGEMENT_PERMISSIONS + ['can_delete_space']
        for perm_codename in direct_space_perms_to_assign:
            try:
                assign_perm(f'spaces.{perm_codename}', current_managed_by, instance)
            except Exception as e:
                logger.error(
                    f"为 {current_managed_by.username} 分配 'spaces.{perm_codename}' 在直接空间 {instance.name} 上时失败: {e}")

        # --- REMOVED: BookableAmenity object-level permission assignment for new manager for direct space ---
        # for ba in instance.bookable_amenities.all().iterator():
        #     for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
        #         try:
        #             assign_perm(f'spaces.{perm_codename}', current_managed_by, ba)
        #         except Exception as e:
        #             logger.error(
        #                 f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for BookableAmenity {ba.id} in space {instance.name}: {e}")
        # --- END REMOVED ---

        # 为层级中的所有父级空间分配查看权限
        parent_space_traversal = instance.parent_space
        processed_parent_pks = set()
        while parent_space_traversal:
            if parent_space_traversal.pk in processed_parent_pks:
                logger.error(
                    f"检测到空间 {instance.name} 起始于 {parent_space_traversal.name} 的循环父级空间引用。停止父级权限分配以防止无限循环。")
                break
            processed_parent_pks.add(parent_space_traversal.pk)

            for perm_codename in SPACE_VIEW_ONLY_PERMISSIONS:
                try:
                    assign_perm(f'spaces.{perm_codename}', current_managed_by, parent_space_traversal)
                except Exception as e:
                    logger.error(
                        f"为 {current_managed_by.username} 分配 'spaces.{perm_codename}' 在父级空间 {parent_space_traversal.name} 上时失败: {e}")

            parent_space_traversal = parent_space_traversal.parent_space

        # 为未管理或由相同人员管理的后代空间分配管理权限
        descendant_spaces = get_all_descendant_spaces(instance)
        for child_space in descendant_spaces:
            if child_space.managed_by is None or child_space.managed_by == current_managed_by:
                for perm_codename in direct_space_perms_to_assign:
                    try:
                        assign_perm(f'spaces.{perm_codename}', current_managed_by, child_space)
                    except Exception as e:
                        logger.error(
                            f"为 {current_managed_by.username} 分配 'spaces.{perm_codename}' 在子空间 {child_space.name} 上时失败: {e}")

                # --- REMOVED: BookableAmenity object-level permission assignment for new manager for child spaces ---
                # for ba_child in child_space.bookable_amenities.all().iterator():
                #     if ba_child.space.managed_by is None or ba_child.space.managed_by == current_managed_by:
                #         for perm_codename in BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS:
                #             try:
                #                 assign_perm(f'spaces.{perm_codename}', current_managed_by, ba_child)
                #             except Exception as e:
                #                 logger.error(
                #                     f"Failed to assign 'spaces.{perm_codename}' to {current_managed_by.username} for BookableAmenity {ba_child.id} in child space {child_space.name}: {e}")
                #     else:
                #         logger.info(
                #             f"Skipping BookableAmenity permission assignment for {ba_child.id} in child space {child_space.name} "
                #             f"from parent manager {current_managed_by.username} (PK:{current_managed_by.pk}), due to child space having a different manager ({ba_child.space.managed_by.username}, PK:{ba_child.space.managed_by.pk}).")
                # --- END REMOVED ---
            else:
                logger.info(
                    f"跳过为子空间 {child_space.name} (PK:{child_space.pk}) 进行自上而下的管理权限分配，"
                    f"因为该子空间已由不同的管理员 ({child_space.managed_by.username}, PK:{child_space.managed_by.pk}) 直接管理。")
    else:
        logger.info(
            f"空间 {instance.name} 没有管理员 (managed_by 为 None)。未通过此空间实例向任何用户分配权限。")

    # --- Step 3: 缓存失效逻辑 (保持不变) ---
    affected_parent_pks = set()
    if old_parent_pk:
        affected_parent_pks.add(old_parent_pk)
    if current_parent_pk:
        affected_parent_pks.add(current_parent_pk)

    if (old_managed_by and (old_managed_by != current_managed_by)) or \
            (current_managed_by and (old_managed_by != current_managed_by or created)):
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
        logger.info(
            f"由于空间 (PK:{instance.pk}) 的管理人员发生变化，已触发全局空间列表缓存失效。")

    if old_space_type_pk != current_space_type_pk:
        if old_space_type_pk:
            invalidate_all_spaces_dependent_on_spacetype.delay(old_space_type_pk)
        if current_space_type_pk:
            invalidate_all_spaces_dependent_on_spacetype.delay(current_space_type_pk)
        logger.info(
            f"由于空间 (PK:{instance.pk}) 的空间类型发生变化，已触发依赖空间缓存失效。")

    if old_permitted_groups_pks != current_permitted_groups_pks:
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
        logger.info(
            f"由于空间 (PK:{instance.pk}) 的可预订用户组发生变化，已触发全局空间列表缓存失效。")

    # 触发核心 Space 缓存失效任务 (处理详情、list_by_parent 和 bookable_amenity 列表)
    invalidate_space_cache.delay(instance.pk, list(affected_parent_pks))
    logger.debug(f"Post_save signal for Space (PK:{instance.pk}). 已触发缓存失效任务。")


@receiver(post_delete, sender=Space)
def space_post_delete_handler(sender, instance, **kwargs):
    """
    当 Space 实例删除后，异步触发缓存失效任务。 (保持不变)
    """
    affected_parent_pks = [instance.parent_space_id] if instance.parent_space_id else []

    if instance.managed_by_id:
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
        CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
        logger.info(
            f"由于空间 (PK:{instance.pk}) 删除影响了管理员，已触发全局空间列表缓存失效。")
    if instance.space_type_id:
        invalidate_all_spaces_dependent_on_spacetype.delay(instance.space_type_id)
        logger.info(
            f"由于空间 (PK:{instance.pk}) 删除影响了空间类型 {instance.space_type_id}，已触发依赖空间缓存失效。")

    # The following line covers the case of permitted_groups change and is a general clear for space lists
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
    logger.info(
        f"由于空间 (PK:{instance.pk}) 删除影响了可预订用户组，已触发全局空间列表缓存失效。")

    invalidate_space_cache.delay(instance.pk, affected_parent_pks)
    logger.debug(f"Post_delete signal for Space (PK:{instance.pk}). 已触发缓存失效任务。")


# ====================================================================
# NEW: Group 模型的信号处理 (保持不变)
# ====================================================================
@receiver(post_save, sender=Group)
@receiver(post_delete, sender=Group)
def group_change_handler(sender, instance, **kwargs):
    """
    当 Group 实例保存 (创建/更新) 或删除后，触发所有可能依赖此 Group 的 Space 列表缓存失效。 (保持不变)
    """
    logger.info(
        f"用户组 '{instance.name}' (PK:{instance.pk}) 已变更/删除。正在触发依赖空间缓存失效。")
    invalidate_all_spaces_dependent_on_group.delay(instance.pk)