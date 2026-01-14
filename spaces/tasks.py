# spaces/tasks.py (完整修订版，添加级联缓存失效任务)
import logging
from celery import shared_task
from core.cache import CacheService
# 导入模型，因为任务中可能需要查询
from spaces.models import Space, BookableAmenity, SpaceType  # Add SpaceType here for new task
from django.db.models import Q  # 用于复杂查询，比如通过外键反向查找

logger = logging.getLogger(__name__)


# --- SpaceType 相关的缓存失效任务 ---
@shared_task
def invalidate_spacetype_cache(pk: int):
    """
    使单个 SpaceType 对象及其列表的缓存失效。
    """
    logger.info(f"Celery Task: Invalidating SpaceType detail cache for PK={pk}")
    CacheService.invalidate_object_cache(key_prefix='spaces:spacetype:detail', pk=pk)
    # 当单个空间类型变化时，所有空间类型列表也需要失效
    CacheService.invalidate_list_cache(key_prefix='spaces:spacetype:list_all', custom_postfix='list_all')
    logger.info(f"Celery Task: Invalidated SpaceType list cache: spaces:spacetype:list_all (with explicit postfix).")


@shared_task
def invalidate_all_spaces_dependent_on_spacetype(spacetype_pk: int):
    """
    使所有依赖于给定 SpaceType 的 Space 对象的缓存失效。
    这包括这些 Space 的详情缓存和所有相关列表缓存。
    """
    logger.info(f"Celery Task: Invalidating all Spaces dependent on SpaceType (PK:{spacetype_pk}).")

    # 明确清理所有 Space 列表 (通用列表、管理员列表、匿名列表，因为 SpaceType 的改变可能影响这些列表的过滤或内容)
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='list_all')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='admin')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='anonymous')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_filtered', custom_postfix='general_list')
    logger.info(f"Celery Task: Invalidated general Space lists due to SpaceType (PK:{spacetype_pk}) change.")

    # 查找到所有关联此 SpaceType 的 Space 对象，并单独失效它们的详情缓存
    # 对于 onDelete=SET_NULL 的情况，我们主要依赖于通用列表失效。
    # 这里的查询是为了确保那些 *直接关联* 的 Space 的详情缓存失效。
    linked_space_pks = list(Space.objects.filter(space_type_id=spacetype_pk).values_list('pk', flat=True))
    for space_pk in linked_space_pks:
        CacheService.invalidate_object_cache(key_prefix='spaces:space:detail', pk=space_pk)
        # 如果 SpaceType 的改变也影响了 Space 下的 BookableAmenity 列表，也需要失效它。
        # 例如，如果 SpaceType 改变了 Space 的可用性，可能间接使得 BookableAmenity 的可预订性发生关联变化。
        CacheService.invalidate_list_cache(key_prefix='spaces:bookable_amenity:list_by_space',
                                           custom_postfix=str(space_pk))
    logger.info(
        f"Celery Task: Invalidated {len(linked_space_pks)} Space detail caches linked to SpaceType (PK:{spacetype_pk}).")


# --- Amenity 相关的缓存失效任务 ---
@shared_task
def invalidate_amenity_cache(pk: int):
    """
    使单个 Amenity 对象及其列表的缓存失效。
    """
    logger.info(f"Celery Task: Invalidating Amenity detail cache for PK={pk}")
    CacheService.invalidate_object_cache(key_prefix='spaces:amenity:detail', pk=pk)
    CacheService.invalidate_list_cache(key_prefix='spaces:amenity:list_all', custom_postfix='list_all')
    logger.info(f"Celery Task: Invalidated Amenity list cache: spaces:amenity:list_all (with explicit postfix).")


@shared_task
def invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity(amenity_pk: int):
    """
    使所有引用给定 Amenity 的 BookableAmenity 及其所属 Space 的缓存失效。
    """
    logger.info(f"Celery Task: Invalidating BookableAmenity and Space caches dependent on Amenity (PK:{amenity_pk}).")

    # 找到所有关联此 Amenity 的 BookableAmenity 对象，并获取它们的 PK 和所属 Space 的 PK
    linked_bookable_amenity_space_pks = list(
        BookableAmenity.objects.filter(amenity_id=amenity_pk).values_list('pk', 'space_id')
    )

    impacted_space_pks = set()
    for ba_pk, space_pk in linked_bookable_amenity_space_pks:
        # 失效每个关联的 BookableAmenity 的详情缓存
        CacheService.invalidate_object_cache(key_prefix='spaces:bookable_amenity:detail', pk=ba_pk)
        # 失效其所属 Space 下的 BookableAmenity 列表缓存
        if space_pk:
            CacheService.invalidate_list_cache(
                key_prefix='spaces:bookable_amenity:list_by_space',
                custom_postfix=str(space_pk)
            )
            impacted_space_pks.add(space_pk)  # 收集所有受影响的 Space PK

    # 失效所有受影响的 Space 详情缓存
    for space_pk in impacted_space_pks:
        CacheService.invalidate_object_cache(key_prefix='spaces:space:detail', pk=space_pk)
        logger.debug(
            f"Celery Task: Invalidated Space detail cache for PK={space_pk} due to Amenity (PK:{amenity_pk}) change.")

    # 最后，由于 Amenity 的改变可能影响 Space 列表（例如：通过 BookableAmenity 的 is_bookable_individually 影响 Space 的可用性），
    # 故需要失效所有 Space 列表。
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='list_all')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='admin')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='anonymous')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_filtered', custom_postfix='general_list')
    logger.info(f"Celery Task: Invalidated general Space lists due to Amenity (PK:{amenity_pk}) change.")


# --- BookableAmenity 相关的缓存失效任务 ---
@shared_task
def invalidate_bookable_amenity_cache(pk: int, space_pk: int):
    """
    使单个 BookableAmenity 对象及其相关列表的缓存失效。
    :param pk: BookableAmenity 的主键
    :param space_pk: BookableAmenity 所属 Space 的主键 (用于失效其所属空间相关的缓存)
    """
    logger.info(f"Celery Task: Invalidating BookableAmenity cache for PK={pk}, Space PK={space_pk}")

    # 1. 失效 BookableAmenity 详情缓存
    CacheService.invalidate_object_cache(key_prefix='spaces:bookable_amenity:detail', pk=pk)

    # 2. 失效其所属 Space 的 BookableAmenity 列表缓存
    if space_pk is not None:
        CacheService.invalidate_list_cache(
            key_prefix='spaces:bookable_amenity:list_by_space',
            custom_postfix=str(space_pk)
        )
        logger.info(f"Celery Task: Invalidated BookableAmenity list for Space PK={space_pk}.")

    # 3. 由于 BookableAmenity 经常作为内联数据包含在 Space 详情中，所以其发生变化会影响 Space 详情。
    #    此外，BookableAmenity 的状态 (is_bookable, quantity) 可能影响 Space 列表的过滤结果。
    if space_pk is not None:
        CacheService.invalidate_object_cache(key_prefix='spaces:space:detail', pk=space_pk)
        logger.info(f"Celery Task: Invalidated Space detail cache for PK={space_pk} due to BookableAmenity change.")

    # 4. 同时，也需要失效所有 Space 列表的缓存，因为 BookableAmenity 的变化也会影响某些 Space 列表的过滤结果或内联数据。
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='list_all')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='admin')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='anonymous')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_filtered', custom_postfix='general_list')


# --- Space 相关的缓存失效任务 ---
@shared_task
def invalidate_space_cache(pk: int, affected_parent_pks: list = None):
    """
    使单个 Space 对象及其相关列表的缓存失效。
    :param pk: 空间主键
    :param affected_parent_pks: 影响的父空间ID列表，用于清除父空间的子空间列表缓存 (包含旧的和新的父空间PKs)
    """
    logger.info(f"Celery Task: Invalidating Space cache for PK={pk}")

    # 1. 失效 Space 详情缓存
    CacheService.invalidate_object_cache(key_prefix='spaces:space:detail', pk=pk)

    # 2. 失效所有 Space 列表缓存 (通用列表、管理员列表、匿名列表)
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='list_all')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='admin')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_all', custom_postfix='anonymous')
    CacheService.invalidate_list_cache(key_prefix='spaces:space:list_filtered', custom_postfix='general_list')
    logger.info(f"Celery Task: Invalidated general Space lists for PK={pk}.")

    # 3. 如果 Space 的 parent_space 发生了变化，需要失效旧的和新的父空间的子空间列表缓存
    if affected_parent_pks:
        for parent_pk in affected_parent_pks:
            if parent_pk is not None:
                CacheService.invalidate_list_cache(
                    key_prefix='spaces:space:list_by_parent',
                    custom_postfix=str(parent_pk)  # 使用父空间ID作为 custom_postfix
                )
                logger.info(f"Celery Task: Invalidated child spaces list for parent PK={parent_pk}.")

    # 4. 失效该 Space 下所有 BookableAmenity 的列表缓存
    #    当 Space 发生变化时，其下 BookableAmenity 的列表内容不变，但其 to_dict 嵌套依赖 Space 信息。
    #    所以最好还是让它失效。
    CacheService.invalidate_list_cache(
        key_prefix='spaces:bookable_amenity:list_by_space',
        custom_postfix=str(pk)  # Custom postfix is the Space ID
    )
    logger.info(f"Celery Task: Invalidated BookableAmenity list for Space PK={pk}.")


@shared_task
def invalidate_space_cache_for_manager(manager_pk: int):
    """
    使某个管理人员或常规用户能查看/管理的 Space 列表缓存失效。
    这个任务用于处理 Space 的 `managed_by` 字段变更，或者用户权限变更时。
    """
    logger.info(f"Celery Task: Invalidating Space list cache for manager/user with PK={manager_pk}")
    # Cache key for space manager list
    CacheService.invalidate_list_cache(
        key_prefix='spaces:space:list_by_manager',
        custom_postfix=str(manager_pk)  # Manager PK is the custom postfix
    )
    # Cache key for a regular user's general space list (if they were viewing general spaces)
    CacheService.invalidate_list_cache(
        key_prefix='spaces:space:list_all',  # Re-using list_all key_prefix but with user specific postfix
        custom_postfix=f'user_{manager_pk}'  # User is the manager, also affecting their general view
    )
    logger.info(f"Celery Task: Invalidated Space lists for manager/user PK={manager_pk}.")