# spaces/tasks.py
import logging
from typing import Optional, List

from celery import shared_task
from core.cache import CacheService
from spaces.models import Space, BookableAmenity, SpaceType
logger = logging.getLogger(__name__)

# --- SpaceType related cache invalidation tasks ---
@shared_task
def invalidate_spacetype_cache(pk: int):
    """
    使单个 SpaceType 对象及其列表的缓存失效。
    """
    logger.info(f"Celery Task: Invalidating SpaceType cache for PK={pk}")
    CacheService.invalidate_object_cache(key_prefix='spaces:spacetype', pk=pk)
    CacheService.invalidate_list_cache(key_prefix='spaces:spacetype', custom_postfix='list_all')
    logger.info(f"Celery Task: Invalidated SpaceType list cache: spaces:spacetype:list_all.")

@shared_task
def invalidate_space_details_and_amenity_lists_in_bulk(space_pks: List[int]):
    """
    一个辅助任务，用于批量失效给定 Space PKs 列表的 Space 详情缓存
    及其关联的 BookableAmenity 列表缓存。
    """
    if not space_pks:
        logger.debug("Celery Task: No space PKs provided for bulk detail/BA list invalidation. Skipping.")
        return

    logger.info(f"Celery Task: Invalidating detail/BA list caches for {len(space_pks)} spaces in bulk.")
    for space_pk in space_pks:
        CacheService.invalidate_object_cache(key_prefix='spaces:space', pk=space_pk)
        CacheService.invalidate_list_cache(
            key_prefix='spaces:bookable_amenity',
            custom_postfix=f'list_by_space:{space_pk}'
        )
    logger.info(f"Celery Task: Done bulk invalidation for Space details and BookableAmenity lists.")

@shared_task
def invalidate_all_spaces_dependent_on_spacetype(spacetype_pk: int):
    """
    使所有依赖于给定 SpaceType 的 Space 对象的缓存失效。
    这包括这些 Space 的详情缓存和所有相关列表缓存。
    """
    logger.info(f"Celery Task: Invalidating all Spaces dependent on SpaceType (PK:{spacetype_pk}).")

    # 1. Invalidate all dynamic Space list caches (global invalidation for query params hash based keys)
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
    logger.info(
        f"Celery Task: Invalidated all Space general/filtered list caches due to SpaceType (PK:{spacetype_pk}) change.")

    # 2. Collect PKs of impacted spaces and trigger bulk invalidation for their detail and BA lists
    linked_space_pks = list(Space.objects.filter(space_type_id=spacetype_pk).values_list('pk', flat=True))
    if linked_space_pks:
        invalidate_space_details_and_amenity_lists_in_bulk.delay(linked_space_pks)
        logger.info(f"Celery Task: Triggered bulk detail/BA list cache invalidation for {len(linked_space_pks)} Spaces linked to SpaceType (PK:{spacetype_pk}).")
    else:
        logger.info(f"Celery Task: No Spaces found dependent on SpaceType (PK:{spacetype_pk}). No detail/BA lists to invalidate.")

# --- Amenity related cache invalidation tasks ---
@shared_task
def invalidate_amenity_cache(pk: int):
    """
    使单个 Amenity 对象及其列表的缓存失效。
    """
    logger.info(f"Celery Task: Invalidating Amenity cache for PK={pk}")
    CacheService.invalidate_object_cache(key_prefix='spaces:amenity', pk=pk)
    CacheService.invalidate_list_cache(key_prefix='spaces:amenity', custom_postfix='list_all')
    logger.info(f"Celery Task: Invalidated Amenity list cache: spaces:amenity:list_all.")

@shared_task
def invalidate_all_bookable_amenities_and_parent_spaces_dependent_on_amenity(amenity_pk: int):
    """
    使所有引用给定 Amenity 的 BookableAmenity 及其所属 Space 的缓存失效。
    """
    logger.info(f"Celery Task: Invalidating BookableAmenity and Space caches dependent on Amenity (PK:{amenity_pk}).")

    # 1. Invalidate all dynamic Space list caches (global invalidation for query params hash based keys)
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
    logger.info(f"Celery Task: Invalidated all Space general/filtered list caches due to Amenity (PK:{amenity_pk}) change.")

    linked_bookable_amenity_space_pks = list(
        BookableAmenity.objects.filter(amenity_id=amenity_pk).values_list('pk', 'space_id')
    )

    impacted_space_pks = set()
    for ba_pk, space_pk in linked_bookable_amenity_space_pks:
        CacheService.invalidate_object_cache(key_prefix='spaces:bookable_amenity', pk=ba_pk)
        if space_pk:
            impacted_space_pks.add(space_pk)

    # 2. Trigger bulk invalidation for impacted spaces' detail and BA lists
    if impacted_space_pks:
        invalidate_space_details_and_amenity_lists_in_bulk.delay(list(impacted_space_pks))
        logger.info(f"Celery Task: Triggered bulk detail/BA list cache invalidation for {len(impacted_space_pks)} Spaces due to Amenity (PK:{amenity_pk}) change.")
    else:
        logger.info(f"Celery Task: No impacted Spaces found for Amenity (PK:{amenity_pk}).")

# --- BookableAmenity related cache invalidation tasks ---
@shared_task
def invalidate_bookable_amenity_cache(pk: int, space_pk: Optional[int]):
    """
    使单个 BookableAmenity 对象及其相关列表的缓存失效。
    """
    logger.info(f"Celery Task: Invalidating BookableAmenity cache for PK={pk}, Space PK={space_pk}")

    CacheService.invalidate_object_cache(key_prefix='spaces:bookable_amenity', pk=pk)

    if space_pk is not None:
        CacheService.invalidate_list_cache(
            key_prefix='spaces:bookable_amenity',
            custom_postfix=f'list_by_space:{space_pk}'
        )
        logger.info(f"Celery Task: Invalidated BookableAmenity list for Space PK={space_pk}.")

        CacheService.invalidate_object_cache(key_prefix='spaces:space', pk=space_pk)
        logger.info(f"Celery Task: Invalidated Space detail cache for PK={space_pk} due to BookableAmenity change.")

    # These global list invalidations may be redundant if already covered by cascade
    # from Space or SpaceType. However, for direct BookableAmenity changes, this ensures
    # any list view of Spaces that might dynamically show BA count changes is refreshed.
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
    logger.info(
        f"Celery Task: Invalidated all Space general/filtered list caches due to BookableAmenity change for PK={pk}.")

# --- Space related cache invalidation tasks ---
@shared_task
def invalidate_space_cache(pk: int, affected_parent_pks: Optional[List[int]] = None):
    """
    使单个 Space 对象及其相关列表的缓存失效。
    此任务专注于与单个 Space PK 及其直接父子关系相关的缓存。
    全局 Space 列表缓存应由更上层（如 SpaceType 或 Group 变化）的任务处理。
    """
    logger.info(f"Celery Task: Invalidating Space cache for PK={pk}")

    # 1. Invalidate Space detail cache
    CacheService.invalidate_object_cache(key_prefix='spaces:space', pk=pk)


    # 2. If parent_space changed, invalidate child space lists for affected parent PKs
    if affected_parent_pks:
        for parent_pk in affected_parent_pks:
            if parent_pk is not None:
                CacheService.invalidate_list_cache(
                    key_prefix='spaces:space',
                    custom_postfix=f'list_by_parent:{parent_pk}'
                )
                logger.info(f"Celery Task: Invalidated child spaces list for parent PK={parent_pk}.")

    # 3. Invalidate BookableAmenity list for this Space
    CacheService.invalidate_list_cache(
        key_prefix='spaces:bookable_amenity',
        custom_postfix=f'list_by_space:{pk}'
    )
    logger.info(f"Celery Task: Invalidated BookableAmenity list for Space PK={pk}.")

@shared_task
def invalidate_all_spaces_dependent_on_group(group_pk: int):
    """
    使所有依赖于给定 User Group 的 Space 列表缓存失效。
    当 Group 信息（如名称）发生变化时，可能影响 Space 的 permitted_groups_display。
    """
    logger.info(f"Celery Task: Invalidating all Space list caches dependent on Group (PK:{group_pk}).")
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
    logger.info(f"Celery Task: Invalidated all Space general/filtered list caches due to Group (PK:{group_pk}) change.")
