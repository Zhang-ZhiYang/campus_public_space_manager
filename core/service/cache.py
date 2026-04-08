# core/service/cache.py
import json
import logging
import hashlib
from typing import Any, Callable, Dict, List, Optional, Union
from django.core.cache import cache
from django.conf import settings
from functools import wraps
import inspect

# Ensure CustomAPIException is correctly imported from its module path:
try:
    from core.utils.exceptions import CustomAPIException
except ImportError:
    class CustomAPIException(Exception):
        def __init__(self, detail=None, code=None, status_code=500):
            super().__init__(detail)
            self.detail = detail
            self.code = code
            self.status_code = status_code


    logging.warning("CustomAPIException not found at core.utils.exceptions, using dummy placeholder.")

logger = logging.getLogger(__name__)


# ServiceResult class (kept here for consistency with other files given)
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
            if isinstance(self.errors, dict):
                error_strings = [f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in self.errors.items()]
                detail_message = f"{self.message}: {'; '.join(error_strings)}"
            else:
                detail_message = f"{self.message}: {'; '.join(map(str, self.errors))}"
        if not detail_message:
            detail_message = self.message or "An error occurred"
        return CustomAPIException(
            detail=detail_message,
            code=self.error_code,
            status_code=self.status_code
        )


# --- Helper for get_object when Service returns dict from cache ---
class CachedDictObject:
    """
    一个简单的包装器，将字典数据模拟成一个具有某些Django模型行为的对象，
    特别是能够通过属性访问键值，并模拟 `pk` 和 `to_dict` 方法。
    DRF 序列化器可能仍会假设所有嵌套对象都是模型实例。
    """

    def __init__(self, data: Dict[str, Any], model_class=None):
        self._data = data
        self._model_class = model_class
        self.pk = data.get('id') if 'id' in data else None

    def __getattr__(self, name):
        if name in self._data:
            return self._data[name]
        elif name == 'pk':
            return self._data.get('id')
        elif name == '_meta' and self._model_class:
            return self._model_class._meta

        if name in ['user', 'space', 'bookable_amenity', 'related_space', 'reviewed_by']:
            return None

        if name in ['check_in_image', 'check_in_qrcode']:
            url_name = f"{name}_url"
            return self._data.get(url_name)

        # 针对 DRF 可能会访问 Foreign Key 对象的反向管理器
        if name.endswith('_set') or name in ['child_spaces', 'bookable_amenities', 'permitted_groups']:
            # 如果这是一个应该被 SerializerMethodField 处理的关系，这里不抛出 AttributeError
            # 而是返回一个空的 QuerySet 模拟对象或 None，以避免在某些情况下崩溃
            # 实际的数据应该通过 CachedDictObject._data 访问
            return None  # 或者返回一个空列表/空 QuerySet 模拟

        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def __dir__(self):
        return list(super().__dir__()) + list(self._data.keys()) + ['pk', '_model_class']

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.pk == other.pk
        if self._model_class and isinstance(other, self._model_class):
            return self.pk == other.pk
        if hasattr(other, 'pk') and self.pk == other.pk:
            return True
        if isinstance(other, dict) and 'id' in other:
            return self.pk == other['id']
        return NotImplemented

    def __hash__(self):
        return hash(self.pk) if self.pk is not None else hash(id(self))

    def to_dict(self, include_related: bool = True) -> dict:
        if include_related:
            return self._data
        else:
            filtered_data = {k: v for k, v in self._data.items() if k not in [
                'user', 'space', 'bookable_amenity', 'related_space', 'reviewed_by', 'check_in_image', 'check_in_qrcode'
            ]}
            return filtered_data


class CacheService:
    """
    一个封装了 Django 缓存操作的通用服务类。
    提供了键生成、数据存取、过期时间管理和错误处理功能。
    支持根据数据类型设置不同的默认过期时间。
    """

    DEFAULT_TIMEOUT_FROM_SETTINGS = settings.CACHES['default'].get('TIMEOUT', 300)

    TIMEOUTS_MAP = {
        'app_data_generic': DEFAULT_TIMEOUT_FROM_SETTINGS,

        'spaces:spacetype:detail': 3600 * 24,
        'spaces:spacetype:list_all': 3600 * 2,

        'spaces:amenity:detail': 3600 * 24,
        'spaces:amenity:list_all': 3600 * 2,

        'spaces:bookable_amenity:detail': 300,
        'spaces:bookable_amenity:list_by_space': 120,

        'spaces:space:detail': 3600 * 24,
        'spaces:space:list_all': 3600 * 12,
        'spaces:space:list_by_parent': 3600 * 6,
        'spaces:space:list_filtered': 3600 * 12,

        'bookings:booking:detail': 2,
        'bookings:booking:list_by_user': 3600 * 2,
        'bookings:booking:list_active': 3600,
        'bookings:violation:detail': 300,
        'bookings:violation:list_by_user': 120,
        'bookings:user_penalty_points:detail': 120,
        'bookings:ban_policy:list_all': 3600,
        'bookings:user_ban:detail': 3600 * 2,
        'bookings:user_ban:list_by_user': 3600 * 2,
        'bookings:daily_limit:detail': 3600,

        'bookings:daily_limit:effective_by_group_spacetype': 3600,
        'bookings:user_exemption:detail': 3600 * 2,
        'bookings:booking_status:detail': 60,
    }

    @classmethod
    def get_timeout_for_key_prefix(cls, key_prefix: str) -> int:
        return cls.TIMEOUTS_MAP.get(key_prefix, cls.DEFAULT_TIMEOUT_FROM_SETTINGS)

    @classmethod
    def generate_key(cls, key_prefix: str, identifier: Union[int, str, None] = None, custom_postfix: str = None,
                     **kwargs) -> str:
        project_key_prefix = settings.CACHES['default'].get('KEY_PREFIX', 'default')
        base_key = f"{project_key_prefix}:{key_prefix}"

        if identifier is not None:
            # 详情缓存键：{项目前缀}:{base_key}:detail:{identifier}:{custom_postfix}
            detail_key_parts = [f"{base_key}:detail:{identifier}"]
            if custom_postfix:
                detail_key_parts.append(custom_postfix)
            return ":".join(detail_key_parts)

        # 列表/复杂查询缓存键：{项目前缀}:{base_key}:{custom_postfix}:{kwargs_hash}
        parts = []
        if custom_postfix:
            parts.append(custom_postfix)

        if kwargs:
            sorted_kwargs = dict(sorted(kwargs.items()))
            kwargs_string = json.dumps(sorted_kwargs, sort_keys=True)
            kwargs_hash = hashlib.md5(kwargs_string.encode('utf-8')).hexdigest()
            parts.append(f"hash_{kwargs_hash}")

        if parts:
            return f"{base_key}:{':'.join(parts)}"
        else:
            logger.warning(
                f"[CacheService] Using implicit generic key for prefix '{key_prefix}'. "
                "Consider providing an explicit `custom_postfix` (e.g., 'list_all') for clarity."
            )
            return f"{base_key}:generic_list"

    @classmethod
    def get(cls, key_prefix: str, identifier: Union[int, str, None] = None, custom_postfix: str = None,
            **kwargs) -> Any:
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
        cache_key = cls.generate_key(key_prefix, identifier, custom_postfix, **kwargs)

        timeout_lookup_key = ""
        if identifier:  # This is a detail cache. Timeout map typically uses 'key_prefix:detail'
            timeout_lookup_key = f"{key_prefix}:detail"
        elif custom_postfix:  # For list caches with custom_postfix, use its root part for timeout lookup
            list_part = custom_postfix.split(":")[0]  # e.g., 'list_all' from 'list_all:user:1'
            timeout_lookup_key = f"{key_prefix}:{list_part}"
        else:
            timeout_lookup_key = key_prefix  # Fallback

        final_timeout = timeout if timeout is not None else cls.get_timeout_for_key_prefix(timeout_lookup_key)

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
        count = 0
        try:
            project_key_prefix = settings.CACHES['default'].get('KEY_PREFIX', 'default')
            pattern = f"{project_key_prefix}:{key_prefix_root}*"
            count = cache.delete_pattern(pattern)
            logger.info(f"[CacheService] Deleted {count} keys matching pattern '{pattern}'.")
        except Exception as e:
            logger.error(f"[CacheService] Error deleting keys by pattern '{key_prefix_root}': {e}")
        return count

    # --- Convenience methods for list caches (primarily for View layer) ---
    @classmethod
    def get_list_cache(cls, key_prefix: str, custom_postfix: str = None, **kwargs) -> Optional[List[Dict[str, Any]]]:
        return cls.get(key_prefix=key_prefix, custom_postfix=custom_postfix, **kwargs)

    @classmethod
    def set_list_cache(cls, key_prefix: str, custom_postfix: str = None, value: Any = None,
                       timeout: Optional[int] = None, **kwargs) -> bool:
        return cls.set(key_prefix=key_prefix, custom_postfix=custom_postfix, value=value, timeout=timeout, **kwargs)

    # --- Decorator for Service layer (detail cache) ---
    @classmethod
    def cache_method(
            cls,
            key_prefix: str,
            identifier_arg: str = 'pk',
            user_arg_name: Optional[str] = None  # 新增 user_arg_name 参数
    ):
        def decorator(func: Callable[..., ServiceResult]) -> Callable[..., ServiceResult]:
            @wraps(func)
            def wrapper(*args, **kwargs) -> ServiceResult:
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()

                actual_identifier = bound_args.arguments.get(identifier_arg)
                if actual_identifier is None:
                    logger.error(
                        f"[CacheService] Decorator @cache_method on '{func.__name__}' missing identifier argument '{identifier_arg}'. No cache operation performed.")
                    return func(*args, **kwargs)

                # 根据 user_arg_name 获取用户实例，并生成用户特定的缓存后缀
                user_specific_postfix = None
                if user_arg_name:
                    user = bound_args.arguments.get(user_arg_name)
                    if user and user.is_authenticated:
                        if user.is_system_admin:
                            user_specific_postfix = 'system_admin'
                        elif user.is_space_manager:
                            user_specific_postfix = f'space_manager:{user.pk}'
                        else:  # 普通用户
                            user_specific_postfix = f'normal_user:{user.pk}'
                    else:  # 匿名用户 (虽然get_space_by_id通常需要认证)
                        user_specific_postfix = 'anonymous'

                cached_data = cls.get(
                    key_prefix=key_prefix,
                    identifier=actual_identifier,
                    custom_postfix=user_specific_postfix  # 将用户特定的后缀传递给 get 方法
                )

                if cached_data is not None:
                    if isinstance(cached_data, ServiceResult):
                        logger.debug(
                            f"[CacheService] ServiceResult HIT for key '{cls.generate_key(key_prefix, actual_identifier, user_specific_postfix)}'.")
                        return cached_data
                    else:
                        logger.debug(
                            f"[CacheService] Raw data HIT for key '{cls.generate_key(key_prefix, actual_identifier, user_specific_postfix)}'. Wrapping in ServiceResult.")
                        return ServiceResult.success_result(data=cached_data)

                service_result = func(*args, **kwargs)

                if service_result.success and service_result.data is not None:
                    cls.set(
                        key_prefix=key_prefix,
                        value=service_result.data,
                        identifier=actual_identifier,
                        custom_postfix=user_specific_postfix  # 将用户特定的后缀传递给 set 方法
                    )
                    logger.debug(
                        f"[CacheService] Cached ServiceResult.data for key '{cls.generate_key(key_prefix, actual_identifier, user_specific_postfix)}'.")
                return service_result

            return wrapper

        return decorator

    # --- Invalidation Helpers ---
    @classmethod
    def invalidate_object_cache(cls, key_prefix: str, pk: Union[int, str]):
        """
        使单个对象的详情缓存失效。
        考虑到详情缓存现在是按用户角色/ID隔离的，这里需要清除所有用户角色的缓存。
        """
        project_key_prefix = settings.CACHES['default'].get('KEY_PREFIX', 'default')
        # 匹配所有用户角色的详情缓存，例如 'project:spaces:space:detail:7:*'
        pattern = f"{project_key_prefix}:{key_prefix}:detail:{pk}:*"
        count = cache.delete_pattern(pattern)
        logger.info(f"Invalidated {count} keys matching pattern '{pattern}' for object detail cache.")

    @classmethod
    def invalidate_list_cache(cls, key_prefix: str, custom_postfix: Optional[str] = None, **kwargs):
        """使特定条件的列表缓存失效。
        这主要用于在知道具体用户角色或查询参数hash时精准失效。
        如果想要清除所有列表相关的缓存，请使用 invalidate_all_related_cache。
        """
        cls.delete(key_prefix, custom_postfix=custom_postfix, **kwargs)
        log_key_parts = []
        if custom_postfix:
            log_key_parts.append(custom_postfix)
        if kwargs:
            sorted_kwargs = dict(sorted(kwargs.items()))
            kwargs_string = json.dumps(sorted_kwargs, sort_keys=True)
            kwargs_hash = hashlib.md5(kwargs_string.encode('utf-8')).hexdigest()
            log_key_parts.append(f"hash_{kwargs_hash}")

        full_log_key = f"{settings.CACHES['default'].get('KEY_PREFIX', 'default')}:{key_prefix}:{':'.join(log_key_parts)}" if log_key_parts else f"{settings.CACHES['default'].get('KEY_PREFIX', 'default')}:{key_prefix}:generic_list"

        logger.info(f"Invalidated list cache for key='{full_log_key}'.")

    @classmethod
    def invalidate_all_related_cache(cls, key_prefix_root: str):
        """
        通过根前缀（如 'spaces:space'）使所有相关的键失效。
        例如，如果修改了一个空间，可能需要清除所有关于这个空间详情的缓存，
        以及所有包含这个空间的列表缓存。
        """
        count = cls.delete_many_by_prefix(key_prefix_root)
        logger.info(f"Invalidated {count} keys for root prefix '{key_prefix_root}'.")