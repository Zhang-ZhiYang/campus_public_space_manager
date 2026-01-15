# spaces/tasks.py
import logging
from typing import Optional, List

from celery import shared_task
from core.cache import CacheService
from spaces.models import Space, BookableAmenity, SpaceType
from django.db.models import Q
from users.models import CustomUser
from spaces.service.space_service import SpaceService  # Import SpaceService to get user role postfix

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
def invalidate_all_spaces_dependent_on_spacetype(spacetype_pk: int):
    """
    使所有依赖于给定 SpaceType 的 Space 对象的缓存失效。
    这包括这些 Space 的详情缓存和所有相关列表缓存。
    """
    logger.info(f"Celery Task: Invalidating all Spaces dependent on SpaceType (PK:{spacetype_pk}).")

    # Invalidate all dynamic Space list caches. Now they only depend on query params hash.
    CacheService.delete_many_by_prefix(
        key_prefix_root='spaces:space:list_all')  # Will match all 'spaces:space:list_all:hash_*'
    CacheService.delete_many_by_prefix(
        key_prefix_root='spaces:space:list_filtered')  # Will match all 'spaces:space:list_filtered:hash_*'
    logger.info(
        f"Celery Task: Invalidated all Space general/filtered list caches due to SpaceType (PK:{spacetype_pk}) change.")

    linked_space_pks = list(Space.objects.filter(space_type_id=spacetype_pk).values_list('pk', flat=True))
    for space_pk in linked_space_pks:
        CacheService.invalidate_object_cache(key_prefix='spaces:space', pk=space_pk)
        CacheService.invalidate_list_cache(key_prefix='spaces:bookable_amenity',
                                           custom_postfix=f'list_by_space:{space_pk}')
    logger.info(
        f"Celery Task: Invalidated {len(linked_space_pks)} Space detail caches linked to SpaceType (PK:{spacetype_pk}).")


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

    linked_bookable_amenity_space_pks = list(
        BookableAmenity.objects.filter(amenity_id=amenity_pk).values_list('pk', 'space_id')
    )

    impacted_space_pks = set()
    for ba_pk, space_pk in linked_bookable_amenity_space_pks:
        CacheService.invalidate_object_cache(key_prefix='spaces:bookable_amenity', pk=ba_pk)
        if space_pk:
            CacheService.invalidate_list_cache(
                key_prefix='spaces:bookable_amenity',
                custom_postfix=f'list_by_space:{space_pk}'
            )
            impacted_space_pks.add(space_pk)

    for space_pk in impacted_space_pks:
        CacheService.invalidate_object_cache(key_prefix='spaces:space', pk=space_pk)
        logger.debug(
            f"Celery Task: Invalidated Space detail cache for PK={space_pk} due to Amenity (PK:{amenity_pk}) change.")

    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
    logger.info(
        f"Celery Task: Invalidated all Space general/filtered list caches due to Amenity (PK:{amenity_pk}) change.")


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

    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
    logger.info(
        f"Celery Task: Invalidated all Space general/filtered list caches due to BookableAmenity change for PK={pk}.")


# --- Space related cache invalidation tasks ---
@shared_task
def invalidate_space_cache(pk: int, affected_parent_pks: Optional[List[int]] = None):
    """
    使单个 Space 对象及其相关列表的缓存失效。
    """
    logger.info(f"Celery Task: Invalidating Space cache for PK={pk}")

    # 1. Invalidate Space detail cache
    CacheService.invalidate_object_cache(key_prefix='spaces:space', pk=pk)

    # 2. Invalidate all Space list caches (now only depends on query params hash)
    #    This will effectively clear all cached Space lists regardless of query parameters.
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
    logger.info(f"Celery Task: Invalidated all Space general/filtered list caches for PK={pk}.")

    # 3. If parent_space changed, invalidate child space lists for affected parent PKs
    if affected_parent_pks:
        for parent_pk in affected_parent_pks:
            if parent_pk is not None:
                CacheService.invalidate_list_cache(
                    key_prefix='spaces:space',
                    custom_postfix=f'list_by_parent:{parent_pk}'
                )
                logger.info(f"Celery Task: Invalidated child spaces list for parent PK={parent_pk}.")

    # 4. Invalidate BookableAmenity list for this Space
    CacheService.invalidate_list_cache(
        key_prefix='spaces:bookable_amenity',
        custom_postfix=f'list_by_space:{pk}'
    )
    logger.info(f"Celery Task: Invalidated BookableAmenity list for Space PK={pk}.")


@shared_task
def invalidate_space_cache_for_manager(manager_pk: int):
    """
    使某个管理人员或常规用户能查看/管理的 Space 列表缓存失效。
    由于列表不再根据用户角色单独缓存，此任务现在只是触发所有 Space 列表的通用失效。
    """
    logger.info(
        f"Celery Task: Invalidating all Space list caches due to manager/user with PK={manager_pk} related change.")

    # Invalidate all dynamic Space list caches. Now they only depend on query params hash.
    # This task now essentially triggers a global invalidation for space lists,
    # as there's no longer a distinct 'user_role_postfix' in the list cache keys for spaces.
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_all')
    CacheService.delete_many_by_prefix(key_prefix_root='spaces:space:list_filtered')
    logger.info(
        f"Celery Task: Invalidated all Space general/filtered list caches related to a manager/user change for PK={manager_pk}.")

    # Keep specific list_by_manager if such keys still exist for other scenarios (e.g., specific reports)
    # However, if your main SpaceListCreateAPIView no longer generates this, it will delete 0 keys.
    CacheService.invalidate_list_cache(
        key_prefix='spaces:space',
        custom_postfix=f'list_by_manager:{manager_pk}'
    )
    logger.info(f"Celery Task: Done invalidating Space lists potentially affecting manager/user PK={manager_pk}.")