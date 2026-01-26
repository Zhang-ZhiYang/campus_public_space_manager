# settings.py
"""
Django settings for core project.
Enhanced for SpaceRes Analysis System.
"""
import os
from pathlib import Path
from datetime import timedelta
from decouple import config, Csv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# ==============================================================================
# 1. 核心安全配置 (Core Security)
# ==============================================================================

# 读取 .env 文件中的 SECRET_KEY
SECRET_KEY = config('SECRET_KEY')

# 读取 .env, 默认为 False
DEBUG = config('DEBUG', default=False, cast=bool)

# 读取 .env, 支持 CSV 格式
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='127.0.0.1,localhost', cast=Csv())

# ==============================================================================
# 2. 应用定义 (Applications)
# ==============================================================================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # --- 第三方库 ---
    'rest_framework',              # DRF 核心
    'rest_framework_simplejwt',    # JWT 认证
    'rest_framework_simplejwt.token_blacklist', # JWT 黑名单
    'corsheaders',                 # 跨域处理
    'django_filters',              # 高级过滤
    'django_celery_beat',          # 定时任务调度
    'drf_spectacular',             # Swagger/OpenAPI 文档
    'guardian',                    # 对象级权限

    # --- 自定义业务模块 ---
    'core',
    'users.apps.UsersConfig',
    'spaces.apps.SpacesConfig',
    'bookings.apps.BookingsConfig',
    'notifications.apps.NotificationsConfig',
    'check_in.apps.CheckInConfig',   # <-- NEW: 注册 check_in 应用
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware', # 必须要在 CommonMiddleware 之前
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'core', 'templates')], # <-- 添加这一行
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# ==============================================================================
# 3. 数据库配置 (Database) - MySQL
# ==============================================================================

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',

        # 强制从 .env 读取，如果这里报错说明 .env 没配好
        'NAME': config('DATABASE_NAME'),
        'USER': config('DATABASE_USER'),
        'PASSWORD': config('DATABASE_PASSWORD'),
        'HOST': config('DATABASE_HOST'),
        'PORT': config('DATABASE_PORT'),

        'CONN_MAX_AGE': config('DB_CONN_MAX_AGE', default=60, cast=int),

        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

# ==============================================================================
# 4. 认证与用户模型 (Authentication)
# ==============================================================================

AUTH_USER_MODEL = 'users.CustomUser'

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# ==============================================================================
# 5. 国际化与时区 (I18n & L10n)
# ==============================================================================

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai' # <-- 建议改为本地时区，例如 'Asia/Shanghai' 或 'Asia/Chongqing'
USE_I18N = True
USE_TZ = True

# ==============================================================================
# 6. 静态文件与媒体文件 (Static & Media)
# ==============================================================================

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [ BASE_DIR / 'static' ]

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media' # <-- NEW: 明确定义 MEDIA_ROOT

# ==============================================================================
# 7. Django REST Framework 配置 (DRF)
# ==============================================================================

REST_FRAMEWORK = {

    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'core.pagination.CustomPageNumberPagination',
    'PAGE_SIZE': 10,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DATETIME_FORMAT': '%Y-%m-%d %H:%M:%S',
    'DATE_FORMAT': '%Y-%m-%d',
    'EXCEPTION_HANDLER': 'core.utils.error_handler.custom_exception_handler',

    # 添加 OpenAPI Schema 渲染器 (用于 drf-spectacular)
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}
AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend', # 这是 Django 默认的用户/组权限
    'guardian.backends.ObjectPermissionBackend',  # <-- 确保添加这一行
)
# ==============================================================================
# 8. JWT 配置 (Simple JWT)
# ==============================================================================

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=config('JWT_ACCESS_TOKEN_LIFETIME', default=60, cast=int)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=config('JWT_REFRESH_TOKEN_LIFETIME_DAYS', default=1, cast=int)),

    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,

    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,

    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',

    # [重要修正] 默认改为 'id'，除非你在 CustomUser 模型里显式定义了 user_id 字段
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# ==============================================================================
# 9. 跨域资源共享 (CORS)
# ==============================================================================

CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL_ORIGINS', default=True, cast=bool)

if not CORS_ALLOW_ALL_ORIGINS:
    CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='http://localhost:3000', cast=Csv())

CORS_ALLOW_CREDENTIALS = True

# ==============================================================================
# 10. Celery & Redis 配置 (缺失部分已补全)
# ==============================================================================

REDIS_CACHE_URL = config('REDIS_URL', default='redis://127.0.0.1:6379/0') # 假设你的 .env 中定义了 REDIS_URL

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_CACHE_URL, # 使用 REDIS_CACHE_URL
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': config('REDIS_MAX_CONNECTIONS', default=100, cast=int),
                'retry_on_timeout': True,
            },
            # NEW: 增加一个超时设置，使 cache.set 中的 timeout 生效
            'TIMEOUT': config('CACHE_TIMEOUT', default=300, cast=int),
        },
        'KEY_PREFIX': config('CACHE_KEY_PREFIX', default='campus_public_space_manager_cache'), # 你项目的独特前缀
        'TIMEOUT': config('CACHE_TIMEOUT', default=300, cast=int), # 这个是缓存本身的默认超时
    }
}

# 从 .env 读取 Celery 配置
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://127.0.0.1:6379/1') # 你的 .env 定义了 /1
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://127.0.0.1:6379/2') # 你的 .env 定义了 /2

CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
# ==============================================================================
# 11. 其他
# ==============================================================================

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# NEW: 用于二维码生成的基础URL, 必须是外部可访问的完整URL
BASE_URL = config('BASE_URL', default='http://127.0.0.1:8000') # 默认值，请在 .env 中设置你的生产环境URL

# ==============================================================================
# 12. DRF Spectacular (OpenAPI/Swagger) 配置
# ==============================================================================

SPECTACULAR_SETTINGS = {
    'TITLE': '公共空间预订管理系统 API',
    'DESCRIPTION': '毕业设计项目：高性能、可扩展的公共空间预订管理系统后端 API 文档。',
    'VERSION': '1.0.0',
    # 其他设置，如认证方案
    'SERVE_INCLUDE_SCHEMA': False, # 不将schema文件本身添加到UI中
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
        'displayOperationId': False,
        'displayRequestDuration': True,
    },
    'CONTACT': {
        'name': '你的名字/团队名称',
        'email': '你的邮箱',
    },
    'LICENSE': {
        'name': '自定义许可证', # 或 'MIT', 'Apache-2.0'
    },
}

# ==============================================================================
# 13. 日志配置 (Logging)
# ==============================================================================

# 由于你提供了 Logging 配置为注释掉的代码，这里先保持注释。
# 如果你需要激活日志功能，请取消注释并在 BASE_DIR 下创建 'logs' 目录。
# LOGGING = {
#     'version': 1,
#     'disable_existing_loggers': False,
#     'formatters': {
#         'verbose': {
#             'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
#             'style': '{',
#         },
#         'simple': {
#             'format': '{levelname} {message}',
#             'style': '{',
#         },
#         'django.server': {
#             'format': '[{asctime}] {message}',
#             'style': '{',
#         },
#     },
#     'handlers': {
#         'console': {
#             'level': 'DEBUG' if DEBUG else 'INFO',
#             'class': 'logging.StreamHandler',
#             'formatter': 'verbose' if DEBUG else 'simple',
#         },
#         'file_debug': {
#             'level': 'DEBUG',
#             'class': 'logging.FileHandler',
#             'filename': BASE_DIR / 'logs/debug.log',
#             'formatter': 'verbose',
#         },
#         'file_error': {
#             'level': 'ERROR',
#             'class': 'logging.FileHandler',
#             'filename': BASE_DIR / 'logs/error.log',
#             'formatter': 'verbose',
#         },
#         'django.server': {
#             'level': 'INFO',
#             'class': 'logging.StreamHandler',
#             'formatter': 'django.server',
#         },
#     },
#     'loggers': {
#         'django': {
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'INFO',
#             'propagate': False,
#         },
#         'django.request': {
#             'handlers': ['file_debug', 'file_error'],
#             'level': 'WARNING' if DEBUG else 'INFO',
#             'propagate': False,
#         },
#         'django.server': {
#             'handlers': ['django.server'],
#             'level': 'INFO',
#             'propagate': False,
#         },
#         'django.db.backends': {
#             'handlers': ['file_debug'],
#             'level': 'DEBUG' if DEBUG else 'INFO',
#             'propagate': False,
#         },
#         'celery': {
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'INFO',
#             'propagate': False,
#         },
#         'core': {
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'DEBUG' if DEBUG else 'INFO',
#             'propagate': False,
#         },
#         'core.cache': {
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'DEBUG' if DEBUG else 'INFO',
#             'propagate': False,
#         },
#         'users': {
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'INFO',
#             'propagate': False,
#         },
#         'spaces': {
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'DEBUG' if DEBUG else 'INFO',
#             'propagate': False,
#         },
#         'bookings': {
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'INFO',
#             'propagate': False,
#         },
#         'notifications': {
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'INFO',
#             'propagate': False,
#         },
#         'check_in': { # NEW: Add check_in app logger
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'DEBUG' if DEBUG else 'INFO',
#             'propagate': False,
#         },
#         '': {
#             'handlers': ['console', 'file_debug', 'file_error'],
#             'level': 'WARNING',
#             'propagate': False,
#         },
#     },
# }
# # Ensure log directory exists if logging is active
# LOG_DIR = BASE_DIR / 'logs'
# if not LOG_DIR.exists():
#     LOG_DIR.mkdir(parents=True, exist_ok=True) # 使用 parents=True, exist_ok=True

EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')

# SMTP 服务器配置
EMAIL_HOST = config('EMAIL_HOST', default='smtp.qq.com')
EMAIL_PORT = config('EMAIL_PORT', default=465, cast=int) # 自动转换为整数

# 安全设置
# QQ邮箱通常使用 SSL (端口 465)
EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=True, cast=bool) # 自动转换为布尔值
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=False, cast=bool)

# 认证信息
EMAIL_HOST_USER = config('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD')

# 默认发件人
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER)
# 用于发送错误报警邮件的地址 (通常与发件人一致)
SERVER_EMAIL = config('SERVER_EMAIL', default=EMAIL_HOST_USER)