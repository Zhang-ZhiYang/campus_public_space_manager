# core/cache.py
import json
import logging
import hashlib
from django.core.cache import cache
from django.conf import settings
from functools import wraps
import inspect # 导入 inspect 模块

logger = logging.getLogger(__name__)

class CacheService:
    """
    一个封装了 Django 缓存操作的通用服务类。
    提供了键生成、数据存取、过期时间管理和错误处理功能。
    支持根据数据类型设置不同的默认过期时间。
    """

    # 从 settings 中获取全局默认缓存超时时间，若未设置则为 5 分钟
    DEFAULT_TIMEOUT = settings.CACHES['default'].get('TIMEOUT', 300)

    # 定义不同数据类型的默认缓存过期时间（单位：秒）
    # 采用 'app_name:model_name:purpose' 或 'app_name_model_name_purpose' 命名约定
    # 这些值可以根据数据更新频率和业务需求进行调整
    TIMEOUTS_MAP = {
        # --- 通用缓存 ---
        'app_data_generic': DEFAULT_TIMEOUT,  # 通用应用数据，用于没有特定分类的临时缓存

        # --- spaces 应用相关的缓存 ---
        'spaces:spacetype:detail': 3600 * 24,  # 单个空间类型详情 (1天) - 变化极少
        'spaces:spacetype:list_all': 3600 * 24,  # 所有空间类型列表 (1天)

        'spaces:amenity:detail': 3600,  # 单个设施类型详情 (1小时) - 变化较少
        'spaces:amenity:list_all': 3600,  # 所有设施类型列表 (1小时)

        'spaces:bookable_amenity:detail': 300,  # 单个可预订设施实例详情 (5分钟)
        'spaces:bookable_amenity:list_by_space': 120,  # 某个空间的可预订设施列表 (2分钟)

        'spaces:space:detail': 300,  # 单个空间详情 (5分钟) - 可能会频繁变动
        'spaces:space:list_all': 120,  # 所有空间列表 (2分钟)
        'spaces:space:list_by_parent': 120,  # 某个父空间下的子空间列表 (2分钟)
        'spaces:space:list_by_manager': 120, # 某个管理人员管理的空间列表 (2分钟)
        'spaces:space:list_filtered': 60, # 复杂的过滤列表，例如通过 `SpaceFilter` 过滤出的数据 (1分钟)

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

        # --- users 应用相关的缓存 (假设你有 CustomUser 模型) ---
        'users:customuser:profile': 600,  # 用户个人资料 (10分钟)
        'users:customuser:list_all': 300,  # 所有用户列表 (5分钟)
        # TODO: 根据你的业务需求，添加更多特定数据类型的过期时间
    }

    @staticmethod
    def _get_timeout(key_prefix: str) -> int:
        """
        根据键前缀获取对应的过期时间，如果未找到则使用默认值。
        """
        return CacheService.TIMEOUTS_MAP.get(key_prefix, CacheService.DEFAULT_TIMEOUT)

    @staticmethod
    def generate_key(key_prefix: str, identifier=None, custom_postfix: str = None, **kwargs) -> str:
        """
        生成缓存键。
        格式：`{project_prefix}:{key_prefix}:{identifier_or_postfix_or_hash}`
        :param key_prefix: 缓存类型前缀，例如 'spaces:spacetype:detail'。
        :param identifier: 对象的唯一标识，例如主键 (PK)。
        :param custom_postfix: 自定义后缀，用于表示列表的特定状态（如 'active', 'pending'）或复杂查询的类型（如 'by_user'）。
                                如果是简单的“所有列表”，建议明确使用 'list_all'。
        :param kwargs: 用于生成复杂列表键的额外参数，会被哈希化以保证唯一性。
        :return: 完整的缓存键字符串。
        """
        # 获取项目级别的缓存前缀，例如 'campus_public_space_manager_cache'
        project_key_prefix = settings.CACHES['default'].get('KEY_PREFIX', 'default')
        base_key = f"{project_key_prefix}:{key_prefix}"

        if identifier is not None:
            # 单个对象详情的键： project_prefix:key_prefix:identifier
            return f"{base_key}:{identifier}"
        elif custom_postfix:
            # 列表或其他带自定义后缀的键： project_prefix:key_prefix:custom_postfix[:kwargs_hash]
            if kwargs:
                # 对复杂的查询参数进行哈希处理
                # 使用 json.dumps + md5 能保证相同参数组合生成相同的哈希值
                sorted_kwargs = dict(sorted(kwargs.items()))
                kwargs_hash = hashlib.md5(json.dumps(sorted_kwargs, sort_keys=True).encode('utf-8')).hexdigest()
                return f"{base_key}:{custom_postfix}:{kwargs_hash}"
            return f"{base_key}:{custom_postfix}"
        else:
            # 如果没有 identifier 也没有 custom_postfix (或为空字符串)，
            # 这通常是一个逻辑问题，因为列表应该有明确的 custom_postfix
            logger.warning(
                f"[CacheService] Using implicit list key for prefix '{key_prefix}'. "
                "Consider providing an explicit `custom_postfix` (e.g., 'list_all') "
                "or using `list_fixed_custom_postfix` in @cache_method for clarity."
            )
            # 更改为更明确的 'list_implicit' 来区分
            return f"{base_key}:list_implicit"

    @staticmethod
    def get(key_prefix: str, identifier=None, custom_postfix: str = None, **kwargs):
        """
        从缓存中获取数据。
        :return: 缓存的数据，如果缓存未命中或发生错误则返回 None。
        """
        cache_key = CacheService.generate_key(key_prefix, identifier, custom_postfix, **kwargs)
        try:
            data = cache.get(cache_key)
            if data is not None:
                logger.debug(f"[CacheService] Cache HIT for key '{cache_key}'.")
            else:
                logger.debug(f"[CacheService] Cache MISS for key '{cache_key}'.")
            return data
        except Exception as e:
            # 记录缓存读取错误，但不影响主业务流程
            logger.error(f"[CacheService] Error getting key '{cache_key}' from cache: {e}")
            return None

    @staticmethod
    def set(key_prefix: str, value, identifier=None, custom_postfix: str = None, timeout: int = None, **kwargs) -> bool:
        """
        将数据设置到缓存中。
        :param value: 要缓存的数据。
                     注意：此处假设 value 已经是可 JSON 序列化的数据（如 dict, list, int, str），
                     或者 Django 的缓存后端能直接处理（如 pickle）。
                     如果你的服务层返回的是 Django Model 对象，你需要在服务层将其转换为 dict。
        :param timeout: 可选的自定义过期时间（秒）。如果为 None，则使用 _get_timeout 确定的值。
        :return: Boolean，表示是否成功设置。
        """
        cache_key = CacheService.generate_key(key_prefix, identifier, custom_postfix, **kwargs)
        final_timeout = timeout if timeout is not None else CacheService._get_timeout(key_prefix)
        try:
            cache.set(cache_key, value, final_timeout)
            logger.debug(f"[CacheService] Set key '{cache_key}' with timeout {final_timeout}s.")
            return True
        except Exception as e:
            logger.error(f"[CacheService] Error setting key '{cache_key}' to cache: {e}")
            return False

    @staticmethod
    def delete(key_prefix: str, identifier=None, custom_postfix: str = None, **kwargs) -> bool:
        """
        从缓存中删除数据。
        :return: Boolean，表示是否成功删除（即使键不存在也返回 True）。
        """
        cache_key = CacheService.generate_key(key_prefix, identifier, custom_postfix, **kwargs)
        try:
            cache.delete(cache_key)
            logger.debug(f"[CacheService] Deleted key '{cache_key}' from cache.")
            return True
        except Exception as e:
            logger.error(f"[CacheService] Error deleting key '{cache_key}' from cache: {e}")
            return False

    @staticmethod
    def delete_many_by_prefix(key_prefix: str) -> int:
        """
        删除给定 key_prefix (如 'spaces:space') 下的所有缓存键。
        ⚠️ 警告：此操作可能成本较高，应谨慎使用，尤其是在生产环境中。
        通常更推荐精确删除，或在特定场景（如批量更新）下使用，且 key_prefix 应该足够具体。
        """
        count = 0
        try:
            # 构造匹配模式，例如 'campus_public_space_manager_cache:spaces:space:*'
            # 使用 settings 中项目级的 KEY_PREFIX 进行拼接
            project_key_prefix = settings.CACHES['default'].get('KEY_PREFIX', 'default')
            pattern = f"{project_key_prefix}:{key_prefix}:*"

            # 使用 cache.delete_pattern 是 django-redis 提供的高效方法
            # 它会使用 SCAN 命令在 Redis 服务器上进行迭代，比 KEYS 更安全
            count = cache.delete_pattern(pattern)
            logger.info(f"[CacheService] Deleted {count} keys matching pattern '{pattern}'.")
        except Exception as e:
            logger.error(f"[CacheService] Error deleting keys by pattern '{key_prefix}': {e}")
        return count

    # --- 方便服务层使用的 Decorator ---
    @staticmethod
    def cache_method(
            key_prefix: str,
            identifier_arg: str = 'pk',  # 方法参数中作为唯一标识符的参数名 (例如 'id', 'pk')
            is_list_cache: bool = False,  # 是否是列表数据缓存，如果是，则 identifier_arg 会被忽略
            custom_postfix_arg: str = None,  # 方法参数中作为自定义后缀的参数名 (例如 'status', 'user_id')
            list_key_kwargs: list = None,  # 如果是列表，哪些 kwargs 需要参与 key 哈希
            # 新增参数: 对于列表缓存，可以硬编码一个 custom_postfix，避免从方法参数中提取
            # 例如对于 'get_all_amenities' 就可以直接用 list_fixed_custom_postfix='list_all'
            list_fixed_custom_postfix: str = None
    ):
        """
        一个用于服务层方法的缓存装饰器。
        它会自动从方法参数中提取标识符，生成缓存键，并管理缓存的存取。
        """

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                # 获取方法的签名，以便按参数名获取参数值
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()  # 填充默认值

                actual_identifier = None
                if not is_list_cache and identifier_arg:
                    actual_identifier = bound_args.arguments.get(identifier_arg)

                # 优先使用 list_fixed_custom_postfix
                actual_custom_postfix = None
                if is_list_cache and list_fixed_custom_postfix:
                    actual_custom_postfix = list_fixed_custom_postfix
                elif custom_postfix_arg:
                    actual_custom_postfix = bound_args.arguments.get(custom_postfix_arg)
                elif is_list_cache and not actual_custom_postfix:
                    # 如果是列表缓存，但既没有固定后缀也没有通过参数传递，采用默认通用后缀
                    actual_custom_postfix = 'list_generic'

                # 收集用于列表键哈希的 kwargs
                key_kwargs_for_hash = {}
                if is_list_cache and list_key_kwargs:
                    for arg_name in list_key_kwargs:
                        # 确保 arg_name 存在于方法参数中，并且值不是 None
                        if arg_name in bound_args.arguments and bound_args.arguments[arg_name] is not None:
                            key_kwargs_for_hash[arg_name] = bound_args.arguments[arg_name]

                # 尝试从缓存获取
                cached_data = CacheService.get(
                    key_prefix=key_prefix,
                    identifier=actual_identifier,
                    custom_postfix=actual_custom_postfix,
                    **key_kwargs_for_hash
                )

                if cached_data is not None:
                    return cached_data  # 缓存命中，直接返回

                # 缓存未命中，执行原始方法获取数据
                result = func(*args, **kwargs)

                # 如果数据不为 None，就存入缓存
                if result is not None:
                    CacheService.set(
                        key_prefix=key_prefix,
                        value=result,
                        identifier=actual_identifier,
                        custom_postfix=actual_custom_postfix,
                        **key_kwargs_for_hash
                    )
                return result

            return wrapper

        return decorator

    # --- 方便服务层使用的 Invalidation Helpers ---
    @staticmethod
    def invalidate_object_cache(key_prefix: str, pk: int):
        """使单个对象的缓存失效。"""
        CacheService.delete(key_prefix, identifier=pk)
        logger.info(f"Invalidated cache for key_prefix='{key_prefix}' with PK='{pk}'.")

    @staticmethod
    def invalidate_list_cache(key_prefix: str, custom_postfix: str = None, **kwargs):
        """使特定条件的列表缓存失效。"""
        # 确保如果 custom_postfix 是 None，我们使用一致的 'list_implicit' 来进行删除
        # 这与 generate_key 的兜底逻辑一致
        actual_postfix = custom_postfix if custom_postfix is not None else 'list_implicit'
        CacheService.delete(key_prefix, custom_postfix=actual_postfix, **kwargs)
        logger.info(
            f"Invalidated list cache for key_prefix='{key_prefix}' (custom_postfix='{custom_postfix}', kwargs={kwargs}).")

    @staticmethod
    def invalidate_all_related_cache(key_prefix_root: str):
        """
        通过根前缀（如 'spaces:space'）使所有相关的键失效。
        例如，如果修改了一个空间，可能需要清除所有关于这个空间详情的缓存，
        以及所有包含这个空间的列表缓存。
        """
        count = CacheService.delete_many_by_prefix(key_prefix_root)
        logger.info(f"Invalidated {count} keys for root prefix '{key_prefix_root}'.")