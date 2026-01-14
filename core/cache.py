# core/cache.py
import json
import logging
import hashlib
from typing import Any, Callable, Dict, List, Optional, Union
from django.core.cache import cache
from django.conf import settings
from functools import wraps
import inspect  # 导入 inspect 模块

from core.utils.exceptions import CustomAPIException  # This import should be fine if core.utils.exceptions exists

logger = logging.getLogger(__name__)


# Assuming CustomAPIException and ServiceResult are defined and importable where needed.
# If ServiceResult is defined within this file, it's fine. If not, ensure it's in core.utils.response.
# For now, I'll include ServiceResult at the end of this file, as it was in the previous provided file.

class CacheService:
    """
    一个封装了 Django 缓存操作的通用服务类。
    提供了键生成、数据存取、过期时间管理和错误处理功能。
    支持根据数据类型设置不同的默认过期时间。
    """

    # 从 settings 中获取全局默认缓存超时时间，若未设置则为 5 分钟
    DEFAULT_TIMEOUT_FROM_SETTINGS = settings.CACHES['default'].get('TIMEOUT', 300)

    # 定义不同数据类型的默认缓存过期时间（单位：秒）
    # 采用 'app_name:model_name:purpose' 或 'app_name_model_name:purpose' 命名约定
    # 这些值可以根据数据更新频率和业务需求进行调整
    TIMEOUTS_MAP = {
        # --- 通用缓存 ---
        'app_data_generic': DEFAULT_TIMEOUT_FROM_SETTINGS,  # 通用应用数据，用于没有特定分类的临时缓存

        # --- spaces 应用相关的缓存 ---
        'spaces:spacetype:detail': 3600 * 24,  # 单个空间类型详情 (1天) - 变化极少
        'spaces:spacetype:list_all': 3600,  # 所有空间类型列表 (1小时)

        'spaces:amenity:detail': 3600,  # 单个设施类型详情 (1小时) - 变化较少
        'spaces:amenity:list_all': 3600,  # 所有设施类型列表 (1小时)

        'spaces:bookable_amenity:detail': 300,  # 单个可预订设施实例详情 (5分钟)
        'spaces:bookable_amenity:list_by_space': 120,  # 某个空间的可预订设施列表 (2分钟)

        'spaces:space:detail': 300,  # 单个空间详情 (5分钟) - 可能会频繁变动
        'spaces:space:list_all': 120,  # 所有空间列表 (2分钟)
        'spaces:space:list_by_parent': 120,  # 某个父空间下的子空间列表 (2分钟)
        'spaces:space:list_by_manager': 120,  # 某个管理人员管理的空间列表 (2分钟)
        'spaces:space:list_filtered': 60,  # 复杂的过滤列表，例如通过 `SpaceFilter` 过滤出的数据 (1分钟)

        # --- bookings 应用相关的缓存 ---
        'bookings:booking:detail': 60,  # 单个预订详情 (1分钟) - 实时性要求高
        'bookings:booking:list_by_user': 60,  # 某个用户的预订列表 (1分钟)
        'bookings:booking:list_active': 30,  # 活跃中的预订列表 (30秒)
        'bookings:violation:detail': 300,  # 单个违约记录详情 (5分钟)
        'bookings:violation:list_by_user': 120,  # 某个用户的违约记录列表 (2分钟)
        'bookings:user_penalty_points:detail': 120,  # 某个用户特定空间类型的违约点数 (2分钟)
        'bookings:ban_policy:list_all': 3600,  # 所有禁用策略列表 (1小时)
        'bookings:user_ban:detail': 60,  # 用户禁用记录详情 (1分钟)
        'bookings:user_ban:list_by_user': 60,  # 某个用户的禁用记录列表 (1分钟)
        'bookings:daily_limit:detail': 3600,  # 每日预订限制详情 (1小时)

        # TODO: 根据你的业务需求，添加更多特定数据类型的过期时间
    }

    @classmethod
    def get_timeout_for_key_prefix(cls, key_prefix: str) -> int:
        """
        根据键前缀获取对应的过期时间，如果未找到则使用默认值。
        """
        return cls.TIMEOUTS_MAP.get(key_prefix, cls.DEFAULT_TIMEOUT_FROM_SETTINGS)

    @classmethod
    def generate_key(cls, key_prefix: str, identifier: Union[int, str, None] = None, custom_postfix: str = None,
                     **kwargs) -> str:
        """
        生成缓存键。
        格式：`{project_prefix}:{key_prefix}:{identifier_or_postfix_or_hash}`
        :param key_prefix: 缓存类型前缀，例如 'spaces:spacetype:detail'。
        :param identifier: 对象的唯一标识，例如主键 (PK)。只能是基本类型。
        :param custom_postfix: 自定义后缀，用于表示列表的特定状态（如 'active', 'pending'）或复杂查询的类型（如 'by_user'）。
                                如果是简单的“所有列表”，建议明确使用 'list_all'。
        :param kwargs: 用于生成复杂列表键的额外参数，会被哈希化以保证唯一性。
        :return: 完整的缓存键字符串。
        """
        project_key_prefix = settings.CACHES['default'].get('KEY_PREFIX', 'default')
        base_key = f"{project_key_prefix}:{key_prefix}"

        if identifier is not None:
            return f"{base_key}:{identifier}"
        elif custom_postfix:
            if kwargs:
                sorted_kwargs = dict(sorted(kwargs.items()))
                kwargs_string = json.dumps(sorted_kwargs, sort_keys=True)
                kwargs_hash = hashlib.md5(kwargs_string.encode('utf-8')).hexdigest()
                return f"{base_key}:{custom_postfix}:{kwargs_hash}"
            return f"{base_key}:{custom_postfix}"
        else:
            logger.warning(
                f"[CacheService] Using implicit list key for prefix '{key_prefix}'. "
                "Consider providing an explicit `custom_postfix` (e.g., 'list_all') "
                "or using `list_fixed_custom_postfix` in @cache_method for clarity."
            )
            # Changed to more explicit 'list_implicit' for fallback
            return f"{base_key}:list_implicit"

    @classmethod
    def get(cls, key_prefix: str, identifier: Union[int, str, None] = None, custom_postfix: str = None,
            **kwargs) -> Any:
        """
        从缓存中获取数据。
        :return: 缓存的数据，如果缓存未命中或发生错误则返回 None。
        """
        cache_key = cls.generate_key(key_prefix, identifier, custom_postfix, **kwargs)
        try:
            data = cache.get(cache_key)
            if data is not None:
                logger.debug(f"[CacheService] Cache HIT for key '{cache_key}'.")
            else:
                logger.debug(f"[CacheService] Cache MISS for key '{cache_key}'.")
            return data
        except Exception as e:
            logger.error(f"[CacheService] Error getting key '{cache_key}' from cache: {e}")
            return None

    @classmethod
    def set(cls, key_prefix: str, value: Any, identifier: Union[int, str, None] = None, custom_postfix: str = None,
            timeout: Optional[int] = None, **kwargs) -> bool:
        """
        将数据设置到缓存中。
        :param value: 要缓存的数据。
                     此处假设 value 已经是可 JSON 序列化的数据（如 dict, list, int, str），
                     或者 Django 的缓存后端能直接处理（如 pickle）。
        :param timeout: 可选的自定义过期时间（秒）。如果为 None，则使用 get_timeout_for_key_prefix 确定的值。
        :return: Boolean，表示是否成功设置。
        """
        cache_key = cls.generate_key(key_prefix, identifier, custom_postfix, **kwargs)
        final_timeout = timeout if timeout is not None else cls.get_timeout_for_key_prefix(key_prefix)
        try:
            cache.set(cache_key, value, final_timeout)
            logger.debug(f"[CacheService] Set key '{cache_key}' with timeout {final_timeout}s.")
            return True
        except Exception as e:
            logger.error(f"[CacheService] Error setting key '{cache_key}' to cache: {e}")
            return False

    @classmethod
    def delete(cls, key_prefix: str, identifier: Union[int, str, None] = None, custom_postfix: str = None,
               **kwargs) -> bool:
        """
        从缓存中删除数据。
        :return: Boolean，表示是否成功删除（即使键不存在也返回 True）。
        """
        cache_key = cls.generate_key(key_prefix, identifier, custom_postfix, **kwargs)
        try:
            cache.delete(cache_key)
            logger.debug(f"[CacheService] Deleted key '{cache_key}' from cache.")
            return True
        except Exception as e:
            logger.error(f"[CacheService] Error deleting key '{cache_key}' from cache: {e}")
            return False

    @classmethod
    def delete_many_by_prefix(cls, key_prefix_root: str) -> int:
        """
        删除给定 key_prefix_root (如 'spaces:space') 下的所有缓存键。
        ⚠️ 警告：此操作可能成本较高，应谨慎使用，尤其是在生产环境中。
        """
        count = 0
        try:
            project_key_prefix = settings.CACHES['default'].get('KEY_PREFIX', 'default')
            pattern = f"{project_key_prefix}:{key_prefix_root}:*"
            count = cache.delete_pattern(pattern)
            logger.info(f"[CacheService] Deleted {count} keys matching pattern '{pattern}'.")
        except Exception as e:
            logger.error(f"[CacheService] Error deleting keys by pattern '{key_prefix_root}': {e}")
        return count

    # --- 列表缓存的便捷方法（主要供 View 层使用） ---
    @classmethod
    def get_list_cache(cls, key_prefix: str, custom_postfix: str, **kwargs) -> Optional[List[Dict[str, Any]]]:
        """
        从缓存中获取列表数据。
        :param key_prefix: 缓存key前缀 (例如 'spaces:space:list_all')
        :param custom_postfix: 列表的自定义后缀 (例如 'admin', 'user_123', 'list_all')
        :param kwargs: 用于生成复杂列表键的额外参数 (例如 filter params)
        :return: 列表数据 (List[Dict]) 或 None
        """
        return cls.get(key_prefix=key_prefix, custom_postfix=custom_postfix, **kwargs)

    @classmethod
    def set_list_cache(cls, key_prefix: str, custom_postfix: str, value: List[Dict[str, Any]],
                       timeout: Optional[int] = None, **kwargs) -> bool:
        """
        将列表数据设置到缓存中。
        :param key_prefix: 缓存key前缀 (例如 'spaces:space:list_all')
        :param custom_postfix: 列表的自定义后缀 (例如 'admin', 'user_123', 'list_all')
        :param value: 要缓存的列表数据 (应为 List[Dict])
        :param timeout: 可选的过期时间（秒）。如果为 None，则使用 get_timeout_for_key_prefix 确定的值。
        :param kwargs: 用于生成复杂列表键的额外参数
        :return: 是否成功设置 (bool)
        """
        return cls.set(key_prefix=key_prefix, custom_postfix=custom_postfix, value=value, timeout=timeout, **kwargs)

    # --- 方便服务层使用的 Decorator (详情缓存) ---
    @classmethod
    def cache_method(
            cls,
            key_prefix: str,
            identifier_arg: str = 'pk',
            custom_postfix_arg: str = None,
            list_key_kwargs: list = None,
            # Not primarily used for detail cache, but kept for consistency with generate_key signature flexibility
            list_fixed_custom_postfix: str = None  # Not primarily used for detail cache
    ):
        """
        一个用于服务层方法的缓存装饰器。
        它会自动从方法参数中提取标识符，生成缓存键，并管理缓存的存取。
        !!! 注意: 此装饰器主要用于 **详情 (detail) 缓存**，即方法返回 `ServiceResult[Dict[str, Any]]`。
        !!! 列表 (list) 缓存逻辑现在由 View 层通过 `set_list_cache` 手动处理，或者 Service 层方法直接返回 `QuerySet`。
        """

        def decorator(func: Callable[..., 'ServiceResult']) -> Callable[..., 'ServiceResult']:
            @wraps(func)
            def wrapper(*args, **kwargs) -> 'ServiceResult':
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()

                actual_identifier = None
                if identifier_arg:
                    actual_identifier = bound_args.arguments.get(identifier_arg)

                actual_custom_postfix = None
                if custom_postfix_arg:
                    actual_custom_postfix = bound_args.arguments.get(custom_postfix_arg)
                elif list_fixed_custom_postfix:
                    actual_custom_postfix = list_fixed_custom_postfix

                # 尝试从缓存获取
                cached_data = cls.get(
                    key_prefix=key_prefix,
                    identifier=actual_identifier,
                    custom_postfix=actual_custom_postfix
                )

                if cached_data is not None:
                    # 如果缓存中存的是 ServiceResult 对象，直接返回
                    # 否则，表示缓存中存的是 data (Dict[str, Any])，需要包装成 ServiceResult
                    if isinstance(cached_data, ServiceResult):
                        logger.debug(
                            f"[CacheService] ServiceResult HIT for key '{cls.generate_key(key_prefix, actual_identifier, actual_custom_postfix)}'.")
                        return cached_data
                    else:
                        logger.debug(
                            f"[CacheService] Raw data HIT for key '{cls.generate_key(key_prefix, actual_identifier, actual_custom_postfix)}'. Wrapping in ServiceResult.")
                        return ServiceResult.success_result(data=cached_data)

                # 缓存未命中，执行原始方法获取数据 (预期返回 ServiceResult)
                service_result = func(*args, **kwargs)

                # 如果原始方法成功且返回了数据，则进行缓存
                if service_result.success and service_result.data is not None:
                    # 缓存 ServiceResult 的 `data` 部分 (预期为 Dict[str, Any])
                    cls.set(
                        key_prefix=key_prefix,
                        value=service_result.data,
                        identifier=actual_identifier,
                        custom_postfix=actual_custom_postfix
                    )
                    logger.debug(
                        f"[CacheService] Cached ServiceResult.data for key '{cls.generate_key(key_prefix, actual_identifier, actual_custom_postfix)}'.")
                return service_result

            return wrapper

        return decorator

    # --- 方便服务层使用的 Invalidation Helpers ---
    @classmethod
    def invalidate_object_cache(cls, key_prefix: str, pk: Union[int, str]):
        """使单个对象的缓存失效。"""
        cls.delete(key_prefix, identifier=pk)
        logger.info(f"Invalidated cache for key_prefix='{key_prefix}' with PK='{pk}'.")

    @classmethod
    def invalidate_list_cache(cls, key_prefix: str, custom_postfix: Optional[str] = None, **kwargs):
        """使特定条件的列表缓存失效。"""
        # Ensure if custom_postfix is None, we use consistent 'list_implicit' for deletion
        actual_postfix = custom_postfix if custom_postfix is not None else 'list_implicit'
        cls.delete(key_prefix, custom_postfix=actual_postfix, **kwargs)
        logger.info(
            f"Invalidated list cache for key_prefix='{key_prefix}' (custom_postfix='{custom_postfix}', kwargs={kwargs}).")

    @classmethod
    def invalidate_all_related_cache(cls, key_prefix_root: str):
        """
        通过根前缀（如 'spaces:space'）使所有相关的键失效。
        例如，如果修改了一个空间，可能需要清除所有关于这个空间详情的缓存，
        以及所有包含这个空间的列表缓存。
        """
        count = cls.delete_many_by_prefix(key_prefix_root)
        logger.info(f"Invalidated {count} keys for root prefix '{key_prefix_root}'.")


# ServiceResult class (kept here as in previous iteration for consistency, or move to core.utils.response)
class ServiceResult:
    def __init__(self, success: bool, message: str = "", data: Any = None, error_code: str = None,
                 status_code: int = 200, errors: Optional[List[str]] = None):
        self.success = success
        self.message = message
        self.data = data
        self.error_code = error_code
        self.status_code = status_code
        self.errors = errors if errors is not None else []

    @classmethod
    def success_result(cls, data: Any = None, message: str = "Success", status_code: int = 200):
        return cls(success=True, data=data, message=message, status_code=status_code)

    @classmethod
    def error_result(cls, message: str = "Error", error_code: str = "unknown_error", status_code: int = 400,
                     errors: Optional[List[str]] = None):
        return cls(success=False, message=message, error_code=error_code, status_code=status_code, errors=errors)

    def to_exception(self):
        detail_message = self.message
        if self.errors:
            detail_message = f"{self.message}: {'; '.join(self.errors)}"

        return CustomAPIException(
            detail=detail_message,
            code=self.error_code,
            status_code=self.status_code
        )