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
    # Fallback if the path is diff, or define a dummy one for testing
    class CustomAPIException(Exception):
        def __init__(self, detail=None, code=None, status_code=500):
            super().__init__(detail)
            self.detail = detail
            self.code = code
            self.status_code = status_code


    logging.warning("CustomAPIException not found at core.utils.exceptions, using dummy placeholder.")

logger = logging.getLogger(__name__)


# ServiceResult class (kept here for consistency with other files given)
# If ServiceResult is in core.service, you should import it from there.
# For now, let's keep it here if it's being used this way.
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
            # 如果 errors 是字典，尝试将其转换为字符串
            if isinstance(self.errors, dict):
                error_strings = [f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in self.errors.items()]
                detail_message = f"{self.message}: {'; '.join(error_strings)}"
            else:  # 否则按预期处理为列表
                detail_message = f"{self.message}: {'; '.join(map(str, self.errors))}"

        # 如果 detail_message 为空，则仅返回 message
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
        # 直接设置pk属性，便于DRF直接访问obj.pk
        self.pk = data.get('id') if 'id' in data else None

    def __getattr__(self, name):
        """
        允许直接访问字典键作为属性。
        关键改进：不再尝试递归包装嵌套字典为 CachedDictObject，
        而是让序列化器通过 SerializerMethodField 直接处理这些嵌套字典。
        """
        if name in self._data:
            return self._data[name]
        elif name == 'pk':  # Defensive check, though self.pk is set in init
            return self._data.get('id')
        elif name == '_meta' and self._model_class:  # DRF有时会访问_meta
            return self._model_class._meta

        # 对于关系字段，如果 Service 返回的是 None，直接返回 None
        # 如果 Service 返回空字典，返回 None，避免 AttributeError
        if name in ['user', 'space', 'bookable_amenity', 'related_space', 'reviewed_by']:
            return None

        # 如果是 ImageField，DRF可能会尝试访问 ImageField.url
        if name in ['check_in_image', 'check_in_qrcode']:
            # 假设你的 to_dict 已经把 URL 放在了 'check_in_image_url'/'check_in_qrcode_url'
            # 或者 ImageField 字段名本身就是 url，则直接返回 None 或者模拟 ImageFile
            # 这里的简单做法是直接返回对应属性的 URL 或 None
            url_name = f"{name}_url"
            return self._data.get(url_name)  # 返回 URL 字符串

        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    # NEW: 添加 __dir__ 方法，帮助 inspect 和 hasattr 更好地工作
    def __dir__(self):
        return list(super().__dir__()) + list(self._data.keys()) + ['pk', '_model_class']

    # Required for some DRF validation/lookup to work with instance context
    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.pk == other.pk
        if self._model_class and isinstance(other, self._model_class):  # Compare with actual model instance
            return self.pk == other.pk
        if hasattr(other, 'pk') and self.pk == other.pk:  # For comparison with other DRF objects
            return True
        if isinstance(other, dict) and 'id' in other:  # Compare with dict if needed
            return self.pk == other['id']
        return NotImplemented

    def __hash__(self):
        return hash(self.pk) if self.pk is not None else hash(id(self))  # Make hashable

    def to_dict(self, include_related: bool = True) -> dict:
        """
        返回原始字典数据，这与模型实例的 to_dict 方法保持一致。
        """
        # 可以根据需要进行深拷贝，但通常直接返回引用即可
        if include_related:
            return self._data
        else:
            # 如果不包含相关联对象，需要创建一个新的字典并排除相关字段
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
        'spaces:space:list_all': 3600 * 12,  # List key will be like spaces:space:list_all:hash_xxxx
        'spaces:space:list_by_parent': 3600 * 6,
        'spaces:space:list_filtered': 3600 * 12,

        'bookings:booking:detail': 60,
        'bookings:booking:list_by_user': 3600 * 2,
        'bookings:booking:list_active': 3600,
        'bookings:violation:detail': 300,
        'bookings:violation:list_by_user': 120,
        'bookings:user_penalty_points:detail': 120,
        'bookings:ban_policy:list_all': 3600,
        'bookings:user_ban:detail': 3600 * 2,
        'bookings:user_ban:list_by_user': 3600 * 2,
        'bookings:daily_limit:detail': 3600,

        'bookings:daily_limit:effective_by_group_spacetype': 3600,  # 新增 DailyBookingLimitService 的缓存
        'bookings:user_exemption:detail': 3600 * 2,  # 新增：用于检查用户在特定空间类型下的活跃豁免状态
        'bookings:booking_status:detail': 60,  # 新增：用于查询预订状态的缓存，可以更短
    }

    @classmethod
    def get_timeout_for_key_prefix(cls, key_prefix: str) -> int:
        """
        根据精确的 key_prefix (例如 'spaces:space:detail' 或 'spaces:space:list_all')
        获取对应的过期时间，如果未找到则使用默认值。
        """
        return cls.TIMEOUTS_MAP.get(key_prefix, cls.DEFAULT_TIMEOUT_FROM_SETTINGS)

    @classmethod
    def generate_key(cls, key_prefix: str, identifier: Union[int, str, None] = None, custom_postfix: str = None,
                     **kwargs) -> str:
        """
        生成缓存键。
        格式：`{project_prefix}:{key_prefix}:detail:{identifier}` (用于详情)
        或  `{project_prefix}:{key_prefix}:{custom_postfix}:{optional_kwargs_hash}` (用于列表)
        :param key_prefix: 基础缓存类型前缀，例如 'spaces:space'。
        :param identifier: 对象的唯一标识，例如主键 (PK)。仅用于详情缓存。
        :param custom_postfix: 自定义后缀，用于表示列表的特定状态（如 'list_all'）。
        :param kwargs: 用于生成复杂列表键的额外参数，会被哈希化以保证唯一性。
        :return: 完整的缓存键字符串。
        """
        project_key_prefix = settings.CACHES['default'].get('KEY_PREFIX', 'default')
        base_key = f"{project_key_prefix}:{key_prefix}"

        if identifier is not None:
            # For detail view: base_key + ':detail:' + identifier
            # e.g., 'campus_public_space_manager_cache:spaces:space:detail:7'
            return f"{base_key}:detail:{identifier}"

        # If no identifier, it's typically a list or a complex query result.
        parts = []
        if custom_postfix:
            parts.append(custom_postfix)

        if kwargs:
            sorted_kwargs = dict(sorted(kwargs.items()))
            kwargs_string = json.dumps(sorted_kwargs, sort_keys=True)
            kwargs_hash = hashlib.md5(kwargs_string.encode('utf-8')).hexdigest()
            # Prefix "hash_" for clarity and to avoid collision with other parts
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

        # Determine the key to use for looking up default timeout in TIMEOUTS_MAP
        # This needs to be the exact string as defined in TIMEOUTS_MAP
        timeout_lookup_key = ""
        if identifier:  # This is a detail cache
            timeout_lookup_key = f"{key_prefix}:detail"  # e.g., "spaces:space:detail"
        elif custom_postfix:  # This is a list cache with a custom postfix
            # Try to match the custom_postfix root with TIMEOUTS_MAP keys
            # e.g., for custom_postfix='list_all', lookup 'spaces:space:list_all'
            # (Assuming custom_postfix itself is one of the TIMEOUTS_MAP list types)
            # Or if custom_postfix might be 'list_all:admin', just extract 'list_all'
            list_part = custom_postfix.split(':')[0]
            timeout_lookup_key = f"{key_prefix}:{list_part}"  # Default to combining base prefix with list part

            # Additional logic for specific contexts that might have unique timeout keys in TIMEOUTS_MAP
            if key_prefix.startswith('bookings:'):  # More specific for bookings related lists
                if key_prefix == 'bookings:booking' and list_part in ['list_by_user', 'list_active']:
                    timeout_lookup_key = f"bookings:booking:{list_part}"
                elif key_prefix == 'bookings:violation' and list_part in ['list_by_user']:
                    timeout_lookup_key = f"bookings:violation:{list_part}"
                elif key_prefix == 'bookings:user_ban' and list_part in ['list_by_user']:
                    timeout_lookup_key = f"bookings:user_ban:{list_part}"

        else:  # Fallback for keys without identifier or specific custom_postfix (e.g., 'spaces:space:generic_list')
            timeout_lookup_key = key_prefix  # or a more general 'app_data_generic'

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
            # Pattern should match any key starting with 'project_prefix:key_prefix_root'
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
            key_prefix: str,  # This should be the base prefix like 'spaces:space'
            identifier_arg: str = 'pk'
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

                cached_data = cls.get(
                    key_prefix=key_prefix,  # Pass the base key_prefix
                    identifier=actual_identifier
                )

                if cached_data is not None:
                    if isinstance(cached_data, ServiceResult):
                        logger.debug(
                            f"[CacheService] ServiceResult HIT for key '{cls.generate_key(key_prefix, actual_identifier)}'.")
                        return cached_data
                    else:
                        logger.debug(
                            f"[CacheService] Raw data HIT for key '{cls.generate_key(key_prefix, actual_identifier)}'. Wrapping in ServiceResult.")
                        return ServiceResult.success_result(data=cached_data)

                service_result = func(*args, **kwargs)

                if service_result.success and service_result.data is not None:
                    cls.set(
                        key_prefix=key_prefix,  # Pass the base key_prefix
                        value=service_result.data,
                        identifier=actual_identifier
                    )
                    logger.debug(
                        f"[CacheService] Cached ServiceResult.data for key '{cls.generate_key(key_prefix, actual_identifier)}'.")
                return service_result

            return wrapper

        return decorator

    # --- Invalidation Helpers ---
    @classmethod
    def invalidate_object_cache(cls, key_prefix: str, pk: Union[int, str]):
        """使单个对象的详情缓存失效。
        :param key_prefix: 基础前缀，例如 'spaces:space', 'spaces:spacetype', 'spaces:amenity'
        :param pk: 对象的ID
        """
        # generate_key will be called by cls.delete and will correctly form 'base:key_prefix:detail:pk'
        cls.delete(key_prefix, identifier=pk)
        # For logging, explicitly show the expected full detail key pattern
        logger.info(
            f"Invalidated cache for key='{settings.CACHES['default'].get('KEY_PREFIX', 'default')}:{key_prefix}:detail:{pk}'.")

    @classmethod
    def invalidate_list_cache(cls, key_prefix: str, custom_postfix: Optional[str] = None, **kwargs):
        """使特定条件的列表缓存失效。
        :param key_prefix: 列表的基础前缀，例如 'spaces:space', 'spaces:spacetype'
        :param custom_postfix: 列表的自定义后缀，例如 'list_all', 'list_by_parent:1'
        :param kwargs: 用于生成复杂列表键的额外参数
        """
        cls.delete(key_prefix, custom_postfix=custom_postfix, **kwargs)
        # For logging, construct the key as generate_key would, for clarity
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
        # This will delete any key starting with 'project_prefix:key_prefix_root'
        count = cls.delete_many_by_prefix(key_prefix_root)
        logger.info(f"Invalidated {count} keys for root prefix '{key_prefix_root}'.")